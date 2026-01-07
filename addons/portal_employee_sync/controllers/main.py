from odoo import http
from odoo.http import request
import json
import logging

_logger = logging.getLogger(__name__)


class PortalEmployeeSyncController(http.Controller):

    def _verify_api_key(self, api_key):
        """Verify API key"""
        valid_key = "d7ce6e48fe7b6dd95283f5c36f6dd791aa83cf65"
        return api_key == valid_key

    @http.route('/api/employees', type='http', auth='public', methods=['POST'], csrf=False, cors='*')
    def create_employee(self, **kwargs):
        """Create OR UPDATE employee from external system with all SharePoint fields"""
        try:
            # Get API key from headers
            api_key = request.httprequest.headers.get('api-key') or \
                      request.httprequest.headers.get('API-Key') or \
                      request.httprequest.headers.get('Authorization', '').replace('Bearer ', '')

            _logger.info(f"========== NEW EMPLOYEE REQUEST ==========")
            _logger.info(f"Received API Key: {api_key}")

            if not api_key or not self._verify_api_key(api_key):
                return self._json_response({
                    'error': 'Invalid API key',
                    'status': 401
                }, 401)

            # Parse JSON data from request body
            try:
                if request.httprequest.data:
                    data = json.loads(request.httprequest.data.decode('utf-8'))
                else:
                    data = request.httprequest.form.to_dict()

                _logger.info(f"📥 Received data: {json.dumps(data, indent=2)}")
            except Exception as e:
                return self._json_response({
                    'error': f'Invalid JSON: {str(e)}',
                    'status': 400
                }, 400)

            # Validate required field
            if not data.get('name'):
                return self._json_response({
                    'error': 'Name required',
                    'status': 400
                }, 400)

            # ============================================
            # 🔥 CRITICAL: CHECK IF EMPLOYEE EXISTS
            # ============================================
            existing_employee = None

            # Method 1: Search by SharePoint Employee ID (MOST RELIABLE)
            if data.get('employee_id'):
                existing_employee = request.env['hr.employee'].sudo().search([
                    ('sharepoint_employee_id', '=', str(data.get('employee_id')))
                ], limit=1)
                if existing_employee:
                    _logger.info(f"✅ Found existing employee by SharePoint ID: {data.get('employee_id')}")

            # Method 2: Search by Name (if no employee_id provided)
            if not existing_employee and data.get('name'):
                existing_employee = request.env['hr.employee'].sudo().search([
                    ('name', '=', data.get('name'))
                ], limit=1)
                if existing_employee:
                    _logger.info(f"✅ Found existing employee by Name: {data.get('name')}")

            # Method 3: Search by first/middle/last name combination
            if not existing_employee:
                if data.get('employee_first_name') and data.get('employee_last_name'):
                    existing_employee = request.env['hr.employee'].sudo().search([
                        ('employee_first_name', '=', data.get('employee_first_name')),
                        ('employee_last_name', '=', data.get('employee_last_name'))
                    ], limit=1)
                    if existing_employee:
                        _logger.info(f"✅ Found existing employee by First+Last Name")

            # BASE EMPLOYEE DATA
            employee_vals = {
                'name': data.get('name'),
                'mobile_phone': data.get('phone'),
                'department_id': self._get_or_create_department(data.get('department')),
                'job_id': self._get_or_create_job(data.get('job_title')),
            }

            # Add SharePoint Employee ID to vals (for tracking)
            if data.get('employee_id'):
                employee_vals['sharepoint_employee_id'] = str(data.get('employee_id'))

            # IMPORTANT: Only set work_email if employee doesn't exist yet
            if not existing_employee:
                employee_vals['work_email'] = data.get('email')
                _logger.info(f"📧 Setting work_email for NEW employee: {data.get('email')}")
            else:
                _logger.info(
                    f"📧 SKIPPING work_email update - employee exists with email: {existing_employee.work_email}")
                # Don't change email if employee already has Azure account
                if existing_employee.azure_user_id:
                    _logger.warning(f"⚠️ Employee has Azure account - work_email is READ-ONLY")

            # SHAREPOINT NAME FIELDS
            if data.get('employee_first_name'):
                employee_vals['employee_first_name'] = data.get('employee_first_name')
                _logger.info(f"✓ First Name: {data.get('employee_first_name')}")

            if data.get('employee_middle_name'):
                employee_vals['employee_middle_name'] = data.get('employee_middle_name')
                _logger.info(f"✓ Middle Name: {data.get('employee_middle_name')}")

            if data.get('employee_last_name'):
                employee_vals['employee_last_name'] = data.get('employee_last_name')
                _logger.info(f"✓ Last Name: {data.get('employee_last_name')}")

            # GENDER - IMPROVED MAPPING
            if data.get('sex'):
                gender_value = str(data.get('sex')).lower().strip()
                _logger.info(f"📝 Processing gender: '{gender_value}'")

                gender_mapping = {
                    'male': 'male',
                    'm': 'male',
                    'female': 'female',
                    'f': 'female',
                    'other': 'other',
                }

                mapped_gender = gender_mapping.get(gender_value)
                if mapped_gender:
                    employee_vals['sex'] = mapped_gender
                    _logger.info(f"✅ Gender set to: {mapped_gender}")
                else:
                    _logger.warning(f"⚠️ Invalid gender value: '{gender_value}'")

            # BIRTHDAY - MULTIPLE FORMAT SUPPORT
            if data.get('birthday'):
                try:
                    from datetime import datetime
                    birthday_str = str(data.get('birthday')).strip()
                    _logger.info(f"📝 Processing birthday: '{birthday_str}'")

                    date_obj = None
                    date_formats = [
                        '%m/%d/%Y',  # 01/15/1990
                        '%Y-%m-%d',  # 1990-01-15
                        '%d/%m/%Y',  # 15/01/1990
                        '%Y/%m/%d',  # 1990/01/15
                        '%d-%m-%Y',  # 15-01-1990
                    ]

                    for fmt in date_formats:
                        try:
                            date_obj = datetime.strptime(birthday_str, fmt)
                            break
                        except:
                            continue

                    if date_obj:
                        employee_vals['birthday'] = date_obj.strftime('%Y-%m-%d')
                        _logger.info(f"✅ Birthday set to: {employee_vals['birthday']}")
                    else:
                        _logger.warning(f"⚠️ Could not parse birthday: '{birthday_str}'")

                except Exception as e:
                    _logger.error(f"❌ Error processing birthday: {e}")

            # PLACE OF BIRTH
            if data.get('place_of_birth'):
                employee_vals['place_of_birth'] = data.get('place_of_birth')
                _logger.info(f"✓ Place of birth: {data.get('place_of_birth')}")

            # MARITAL STATUS - IMPROVED MAPPING
            if data.get('marital'):
                marital_value = str(data.get('marital')).lower().strip()
                _logger.info(f"📝 Processing marital status: '{marital_value}'")

                marital_mapping = {
                    'single': 'single',
                    'unmarried': 'single',
                    'married': 'married',
                    'cohabitant': 'cohabitant',
                    'living together': 'cohabitant',
                    'widower': 'widower',
                    'widow': 'widower',
                    'divorced': 'divorced',
                }

                mapped_marital = marital_mapping.get(marital_value)
                if mapped_marital:
                    employee_vals['marital'] = mapped_marital
                    _logger.info(f"✅ Marital status set to: {mapped_marital}")
                else:
                    _logger.warning(f"⚠️ Invalid marital status: '{marital_value}'")

            # PRIVATE EMAIL
            if data.get('private_email'):
                employee_vals['private_email'] = data.get('private_email')
                _logger.info(f"✓ Private email: {data.get('private_email')}")

            # NATIONALITY (COUNTRY) - IMPROVED SEARCH
            if data.get('country_id'):
                country_name = str(data.get('country_id')).strip()
                _logger.info(f"📝 Processing country: '{country_name}'")

                country = request.env['res.country'].sudo().search([
                    '|', '|',
                    ('name', '=ilike', country_name),
                    ('name', 'ilike', country_name),
                    ('code', '=ilike', country_name)
                ], limit=1)

                if country:
                    employee_vals['country_id'] = country.id
                    _logger.info(f"✅ Country set to: {country.name} (ID: {country.id})")
                else:
                    _logger.warning(f"⚠️ Country not found: '{country_name}'")

            # MOTHER TONGUE
            if data.get('mother_tongue_id'):
                lang_name = str(data.get('mother_tongue_id')).strip()
                _logger.info(f"📝 Processing mother tongue: '{lang_name}'")

                lang = request.env['res.lang'].sudo().search([
                    '|', '|', '|',
                    ('name', '=ilike', lang_name),
                    ('name', 'ilike', lang_name),
                    ('iso_code', '=ilike', lang_name),
                    ('code', '=ilike', lang_name)
                ], limit=1)

                if lang:
                    employee_vals['mother_tongue_id'] = lang.id
                    _logger.info(f"✅ Mother tongue set to: {lang.name} (ID: {lang.id})")
                else:
                    _logger.warning(f"⚠️ Language '{lang_name}' not found in Odoo")

            # LANGUAGES KNOWN
            if data.get('language_known_ids'):
                try:
                    lang_string = str(data.get('language_known_ids')).strip()
                    _logger.info(f"📝 Processing languages known: '{lang_string}'")

                    lang_names = [l.strip() for l in lang_string.split(',') if l.strip()]
                    _logger.info(f"📋 Split into: {lang_names}")

                    if lang_names:
                        found_langs = request.env['res.lang'].sudo()

                        for lang_name in lang_names:
                            lang = request.env['res.lang'].sudo().search([
                                '|', '|', '|',
                                ('name', '=ilike', lang_name),
                                ('name', 'ilike', lang_name),
                                ('iso_code', '=ilike', lang_name),
                                ('code', '=ilike', lang_name)
                            ], limit=1)

                            if lang:
                                found_langs |= lang
                                _logger.info(f"  ✓ Found: {lang.name}")
                            else:
                                _logger.warning(f"  ✗ Not found: {lang_name}")

                        if found_langs:
                            employee_vals['language_known_ids'] = [(6, 0, found_langs.ids)]
                            _logger.info(f"✅ Languages set: {', '.join(found_langs.mapped('name'))}")
                        else:
                            _logger.warning(f"⚠️ No languages found from: {lang_names}")

                except Exception as e:
                    _logger.error(f"❌ Error processing languages: {e}")

            # ============================================
            # 🔥 CREATE OR UPDATE EMPLOYEE
            # ============================================
            if existing_employee:
                # UPDATE EXISTING EMPLOYEE
                _logger.info(f"📝 UPDATING existing employee: {existing_employee.name} (ID: {existing_employee.id})")
                _logger.info(f"   Current email: {existing_employee.work_email}")
                _logger.info(f"   Azure User ID: {existing_employee.azure_user_id or 'None'}")
                _logger.info(f"   Update values: {json.dumps(employee_vals, default=str, indent=2)}")

                existing_employee.write(employee_vals)
                employee = existing_employee

                _logger.info(f"✅ Employee UPDATED successfully: {employee.name} (ID: {employee.id})")
            else:
                # CREATE NEW EMPLOYEE
                _logger.info(f"🆕 Creating NEW employee with values: {json.dumps(employee_vals, default=str, indent=2)}")
                employee = request.env['hr.employee'].sudo().create(employee_vals)
                _logger.info(f"✅ Employee CREATED successfully: {employee.name} (ID: {employee.id})")

            _logger.info(f"========== REQUEST COMPLETE ==========\n")

            # RETURN DETAILED RESPONSE
            return self._json_response({
                'success': True,
                'status': 'success',
                'employee_id': employee.id,
                'message': f'Employee {"updated" if existing_employee else "created"} successfully',
                'action': 'update' if existing_employee else 'create',
                'data': {
                    'id': employee.id,
                    'name': employee.name,
                    'email': employee.work_email or '',
                    'phone': employee.mobile_phone or '',
                    'first_name': employee.employee_first_name or '',
                    'middle_name': employee.employee_middle_name or '',
                    'last_name': employee.employee_last_name or '',
                    'department': employee.department_id.name if employee.department_id else '',
                    'job_title': employee.job_id.name if employee.job_id else '',
                    'gender': employee.sex or '',
                    'birthday': employee.birthday.strftime('%Y-%m-%d') if employee.birthday else '',
                    'place_of_birth': employee.place_of_birth or '',
                    'marital': employee.marital or '',
                    'private_email': employee.private_email or '',
                    'country': employee.country_id.name if employee.country_id else '',
                    'mother_tongue': employee.mother_tongue_id.name if employee.mother_tongue_id else '',
                    'languages_known': ', '.join(
                        employee.language_known_ids.mapped('name')) if employee.language_known_ids else '',
                    'sharepoint_id': employee.sharepoint_employee_id or '',
                }
            })

        except Exception as e:
            _logger.error(f"❌ Error creating/updating employee: {str(e)}", exc_info=True)
            return self._json_response({
                'error': str(e),
                'status': 500
            }, 500)

    @http.route('/api/employees', type='http', auth='public', methods=['GET'], csrf=False, cors='*')
    def get_employees(self, **kwargs):
        """Get all employees"""
        try:
            # Get API key from headers
            api_key = request.httprequest.headers.get('api-key') or \
                      request.httprequest.headers.get('API-Key') or \
                      request.httprequest.headers.get('Authorization', '').replace('Bearer ', '')

            if not api_key or not self._verify_api_key(api_key):
                return self._json_response({
                    'error': 'Invalid API key',
                    'status': 401
                }, 401)

            # Get all employees
            employees = request.env['hr.employee'].sudo().search([])

            employee_list = []
            for emp in employees:
                employee_list.append({
                    'id': emp.id,
                    'name': emp.name,
                    'email': emp.work_email or '',
                    'phone': emp.mobile_phone or '',
                    'first_name': emp.employee_first_name or '',
                    'middle_name': emp.employee_middle_name or '',
                    'last_name': emp.employee_last_name or '',
                    'department': emp.department_id.name if emp.department_id else '',
                    'job_title': emp.job_id.name if emp.job_id else '',
                    'gender': emp.sex or '',
                    'marital': emp.marital or '',
                    'mother_tongue': emp.mother_tongue_id.name if emp.mother_tongue_id else '',
                    'languages_known': ', '.join(
                        emp.language_known_ids.mapped('name')) if emp.language_known_ids else '',
                    'sharepoint_id': emp.sharepoint_employee_id or '',
                })

            return self._json_response({
                'success': True,
                'status': 'success',
                'count': len(employee_list),
                'employees': employee_list
            })

        except Exception as e:
            _logger.error(f"Error fetching employees: {str(e)}")
            return self._json_response({
                'error': str(e),
                'status': 500
            }, 500)

    def _get_or_create_department(self, dept_name):
        """Get or create department"""
        if not dept_name:
            return False

        department = request.env['hr.department'].sudo().search([
            ('name', '=', dept_name)
        ], limit=1)

        if not department:
            department = request.env['hr.department'].sudo().create({
                'name': dept_name
            })
            _logger.info(f"Created new department: {dept_name}")

        return department.id

    def _get_or_create_job(self, job_title):
        """Get or create job position"""
        if not job_title:
            return False

        job = request.env['hr.job'].sudo().search([
            ('name', '=', job_title)
        ], limit=1)

        if not job:
            job = request.env['hr.job'].sudo().create({
                'name': job_title
            })
            _logger.info(f"Created new job: {job_title}")

        return job.id

    def _json_response(self, data, status=200):
        """Return JSON response with proper headers"""
        return request.make_response(
            json.dumps(data, indent=2),
            headers=[
                ('Content-Type', 'application/json'),
                ('Access-Control-Allow-Origin', '*'),
            ],
            status=status
        )