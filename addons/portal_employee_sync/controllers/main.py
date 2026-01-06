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

    def _extract_sharepoint_value(self, field_data):
        """Extract 'Value' from SharePoint JSON object"""
        if not field_data:
            return None
        if field_data == '':
            return None
        if isinstance(field_data, str):
            field_data = field_data.strip()
            if field_data.startswith('{') and '"Value"' in field_data:
                try:
                    parsed = json.loads(field_data)
                    value = parsed.get('Value', field_data)
                    return value if value != '' else None
                except:
                    return field_data
            return field_data if field_data != '' else None
        if isinstance(field_data, dict):
            value = field_data.get('Value', field_data)
            return value if value != '' else None
        return field_data

    @http.route('/api/employees', type='http', auth='public', methods=['POST'], csrf=False, cors='*')
    def create_employee(self, **kwargs):
        """Create or update employee from SharePoint"""
        try:
            api_key = request.httprequest.headers.get('api-key') or \
                      request.httprequest.headers.get('API-Key') or \
                      request.httprequest.headers.get('Authorization', '').replace('Bearer ', '')

            _logger.info("=" * 50)
            _logger.info("NEW EMPLOYEE API REQUEST")
            _logger.info("=" * 50)

            if not api_key or not self._verify_api_key(api_key):
                _logger.error("❌ Invalid API key")
                return self._json_response({'error': 'Invalid API key', 'status': 401}, 401)

            try:
                if request.httprequest.data:
                    data = json.loads(request.httprequest.data.decode('utf-8'))
                else:
                    data = request.httprequest.form.to_dict()
                _logger.info(f"📥 RAW DATA:\n{json.dumps(data, indent=2)}")
            except Exception as e:
                _logger.error(f"❌ JSON parse error: {e}")
                return self._json_response({'error': f'Invalid JSON: {str(e)}', 'status': 400}, 400)

            if not data.get('name'):
                _logger.error("❌ Name field missing")
                return self._json_response({'error': 'Name required', 'status': 400}, 400)

            # SEARCH FOR EXISTING (EXACT MATCH ONLY)
            existing_employee = None
            if data.get('email'):
                existing_employee = request.env['hr.employee'].sudo().search([
                    ('work_email', '=', data.get('email'))
                ], limit=1)
                if existing_employee:
                    _logger.info(f"✅ FOUND by email: {existing_employee.name} (ID: {existing_employee.id})")

            if not existing_employee and data.get('name'):
                existing_employee = request.env['hr.employee'].sudo().search([
                    ('name', '=', data.get('name'))  # EXACT match only
                ], limit=1)
                if existing_employee:
                    _logger.info(f"✅ FOUND by name: {existing_employee.name} (ID: {existing_employee.id})")

            is_update = bool(existing_employee)
            if is_update:
                _logger.info(f"🔄 MODE: UPDATE (ID: {existing_employee.id})")
                employee = existing_employee
            else:
                _logger.info("🆕 MODE: CREATE NEW")

            # BUILD VALUES WITH EXTRACTION
            name_val = self._extract_sharepoint_value(data.get('name')) or data.get('name')
            email_val = self._extract_sharepoint_value(data.get('email')) or data.get('email')
            phone_val = self._extract_sharepoint_value(data.get('phone')) or data.get('phone')
            dept_val = self._extract_sharepoint_value(data.get('department')) or data.get('department')
            job_val = self._extract_sharepoint_value(data.get('job_title')) or data.get('job_title')

            _logger.info(f"📝 Extracted name: '{name_val}'")
            _logger.info(f"📝 Extracted email: '{email_val}'")
            _logger.info(f"📝 Extracted phone: '{phone_val}'")
            _logger.info(f"📝 Extracted department: '{dept_val}'")
            _logger.info(f"📝 Extracted job: '{job_val}'")

            employee_vals = {
                'name': name_val,
                'work_email': email_val,
                'mobile_phone': phone_val,
                'department_id': self._get_or_create_department(dept_val),
                'job_id': self._get_or_create_job(job_val),
                'employee_type': 'employee',  # CRITICAL FOR ODOO 19
                'active': True,
            }

            _logger.info("✅ Set employee_type='employee' and active=True for Odoo 19")

            # Name fields
            for field in ['employee_first_name', 'employee_middle_name', 'employee_last_name']:
                if data.get(field):
                    val = self._extract_sharepoint_value(data.get(field)) or data.get(field)
                    employee_vals[field] = val
                    _logger.info(f"✓ {field}: {val}")

            # GENDER
            if data.get('sex'):
                gender_raw = self._extract_sharepoint_value(data.get('sex'))
                _logger.info(f"📝 Gender RAW: '{data.get('sex')}' → EXTRACTED: '{gender_raw}'")

                if gender_raw:
                    gender_value = str(gender_raw).lower().strip()
                    gender_mapping = {'male': 'male', 'm': 'male', 'female': 'female', 'f': 'female', 'other': 'other'}
                    if gender_value in gender_mapping:
                        employee_vals['gender'] = gender_mapping[gender_value]
                        _logger.info(f"✅ Gender SET: {gender_mapping[gender_value]}")
                    else:
                        _logger.warning(f"⚠️ Invalid gender: '{gender_value}'")

            # BIRTHDAY
            if data.get('birthday'):
                try:
                    from datetime import datetime
                    birthday_str = str(data.get('birthday')).strip()
                    for fmt in ['%m/%d/%Y', '%Y-%m-%d', '%d/%m/%Y', '%Y/%m/%d', '%d-%m-%Y']:
                        try:
                            date_obj = datetime.strptime(birthday_str, fmt)
                            employee_vals['birthday'] = date_obj.strftime('%Y-%m-%d')
                            _logger.info(f"✅ Birthday SET: {employee_vals['birthday']}")
                            break
                        except:
                            continue
                except Exception as e:
                    _logger.error(f"❌ Birthday error: {e}")

            if data.get('place_of_birth'):
                employee_vals['place_of_birth'] = data.get('place_of_birth')
                _logger.info(f"✓ Place of birth: {data.get('place_of_birth')}")

            # MARITAL
            if data.get('marital'):
                marital_raw = self._extract_sharepoint_value(data.get('marital'))
                _logger.info(f"📝 Marital RAW: '{data.get('marital')}' → EXTRACTED: '{marital_raw}'")

                if marital_raw:
                    marital_value = str(marital_raw).lower().strip()
                    marital_mapping = {
                        'single': 'single', 'unmarried': 'single', 'un married': 'single',
                        'married': 'married', 'cohabitant': 'cohabitant', 'living together': 'cohabitant',
                        'widower': 'widower', 'widow': 'widower', 'divorced': 'divorced'
                    }
                    if marital_value in marital_mapping:
                        employee_vals['marital'] = marital_mapping[marital_value]
                        _logger.info(f"✅ Marital SET: {marital_mapping[marital_value]}")
                    else:
                        _logger.warning(f"⚠️ Invalid marital: '{marital_value}'")

            if data.get('private_email'):
                employee_vals['private_email'] = data.get('private_email')

            # COUNTRY
            if data.get('country_id'):
                country_raw = self._extract_sharepoint_value(data.get('country_id'))
                _logger.info(f"📝 Country RAW: '{data.get('country_id')}' → EXTRACTED: '{country_raw}'")

                if country_raw:
                    country_name = str(country_raw).strip()
                    nationality_map = {
                        'indian': 'India', 'american': 'United States', 'british': 'United Kingdom',
                        'emirati': 'United Arab Emirates', 'pakistani': 'Pakistan',
                        'bangladeshi': 'Bangladesh', 'sri lankan': 'Sri Lanka', 'nepali': 'Nepal',
                        'filipino': 'Philippines'
                    }
                    if country_name.lower() in nationality_map:
                        country_name = nationality_map[country_name.lower()]

                    country = request.env['res.country'].sudo().search([('name', '=', country_name)], limit=1)
                    if not country:
                        country = request.env['res.country'].sudo().search([
                            '|', ('name', 'ilike', country_name), ('code', '=ilike', country_name)
                        ], limit=1)
                    if country:
                        employee_vals['country_id'] = country.id
                        _logger.info(f"✅ Country SET: {country.name}")

            # MOTHER TONGUE
            if data.get('mother_tongue_id'):
                lang_raw = self._extract_sharepoint_value(data.get('mother_tongue_id'))
                _logger.info(f"📝 Mother Tongue RAW: '{data.get('mother_tongue_id')}' → EXTRACTED: '{lang_raw}'")

                if lang_raw:
                    lang_name = str(lang_raw).strip()
                    lang = request.env['res.lang'].sudo().search([
                        '|', '|', '|',
                        ('name', '=ilike', lang_name), ('name', 'ilike', lang_name),
                        ('iso_code', '=ilike', lang_name), ('code', '=ilike', lang_name)
                    ], limit=1)
                    if lang:
                        employee_vals['mother_tongue_id'] = lang.id
                        _logger.info(f"✅ Mother Tongue SET: {lang.name}")
                    else:
                        _logger.warning(f"⚠️ Mother tongue '{lang_name}' not found")

            # LANGUAGES KNOWN
            if data.get('language_known_ids'):
                try:
                    lang_raw = self._extract_sharepoint_value(data.get('language_known_ids'))
                    _logger.info(f"📝 Languages RAW: '{data.get('language_known_ids')}' → EXTRACTED: '{lang_raw}'")

                    if lang_raw:
                        lang_string = str(lang_raw).strip()
                        lang_names = [l.strip() for l in lang_string.split(',') if l.strip()]
                        _logger.info(f"📋 Split languages: {lang_names}")

                        if lang_names:
                            found_langs = request.env['res.lang'].sudo()
                            for lang_name in lang_names:
                                lang = request.env['res.lang'].sudo().search([
                                    '|', '|', '|',
                                    ('name', '=ilike', lang_name), ('name', 'ilike', lang_name),
                                    ('iso_code', '=ilike', lang_name), ('code', '=ilike', lang_name)
                                ], limit=1)
                                if lang:
                                    found_langs |= lang
                                    _logger.info(f"  ✓ Found: {lang.name}")
                                else:
                                    _logger.warning(f"  ✗ Not found: {lang_name}")

                            if found_langs:
                                employee_vals['language_known_ids'] = [(6, 0, found_langs.ids)]
                                _logger.info(f"✅ Languages SET: {', '.join(found_langs.mapped('name'))}")
                except Exception as e:
                    _logger.error(f"❌ Languages error: {e}")

            # CREATE OR UPDATE
            _logger.info(f"📦 Final values:\n{json.dumps(employee_vals, default=str, indent=2)}")

            if is_update:
                _logger.info(f"🔄 UPDATING employee ID: {employee.id}")
                employee.sudo().write(employee_vals)
                _logger.info(f"✅ UPDATED: {employee.name} (ID: {employee.id})")
                message = 'Employee updated successfully'
                status = 'updated'
            else:
                _logger.info("🆕 CREATING new employee")
                employee = request.env['hr.employee'].sudo().create(employee_vals)
                _logger.info(f"✅ CREATED: {employee.name} (ID: {employee.id})")
                _logger.info(f"   Type: {employee.employee_type}")
                _logger.info(f"   Active: {employee.active}")
                message = 'Employee created successfully'
                status = 'created'

            _logger.info("=" * 50)
            _logger.info("REQUEST COMPLETE")
            _logger.info("=" * 50)

            return self._json_response({
                'success': True,
                'status': status,
                'employee_id': employee.id,
                'message': message,
                'data': {
                    'id': employee.id,
                    'name': employee.name,
                    'email': employee.work_email or '',
                    'phone': employee.mobile_phone or '',
                    'employee_type': employee.employee_type,
                    'active': employee.active,
                    'first_name': employee.employee_first_name or '',
                    'middle_name': employee.employee_middle_name or '',
                    'last_name': employee.employee_last_name or '',
                    'department': employee.department_id.name if employee.department_id else '',
                    'job_title': employee.job_id.name if employee.job_id else '',
                    'gender': employee.gender or '',
                    'birthday': employee.birthday.strftime('%Y-%m-%d') if employee.birthday else '',
                    'place_of_birth': employee.place_of_birth or '',
                    'marital': employee.marital or '',
                    'private_email': employee.private_email or '',
                    'country': employee.country_id.name if employee.country_id else '',
                    'mother_tongue': employee.mother_tongue_id.name if employee.mother_tongue_id else '',
                    'languages_known': ', '.join(
                        employee.language_known_ids.mapped('name')) if employee.language_known_ids else '',
                }
            })

        except Exception as e:
            _logger.error(f"❌ FATAL ERROR: {str(e)}", exc_info=True)
            return self._json_response({'error': str(e), 'status': 500}, 500)

    @http.route('/api/employees', type='http', auth='public', methods=['GET'], csrf=False, cors='*')
    def get_employees(self, **kwargs):
        """Get all employees"""
        try:
            api_key = request.httprequest.headers.get('api-key') or \
                      request.httprequest.headers.get('API-Key') or \
                      request.httprequest.headers.get('Authorization', '').replace('Bearer ', '')

            if not api_key or not self._verify_api_key(api_key):
                return self._json_response({'error': 'Invalid API key', 'status': 401}, 401)

            employees = request.env['hr.employee'].sudo().search([])
            employee_list = []
            for emp in employees:
                employee_list.append({
                    'id': emp.id,
                    'name': emp.name,
                    'email': emp.work_email or '',
                    'phone': emp.mobile_phone or '',
                    'employee_type': emp.employee_type,
                    'active': emp.active,
                    'first_name': emp.employee_first_name or '',
                    'middle_name': emp.employee_middle_name or '',
                    'last_name': emp.employee_last_name or '',
                    'department': emp.department_id.name if emp.department_id else '',
                    'job_title': emp.job_id.name if emp.job_id else '',
                    'gender': emp.gender or '',
                    'marital': emp.marital or '',
                    'mother_tongue': emp.mother_tongue_id.name if emp.mother_tongue_id else '',
                    'languages_known': ', '.join(
                        emp.language_known_ids.mapped('name')) if emp.language_known_ids else '',
                })

            return self._json_response({
                'success': True,
                'status': 'success',
                'count': len(employee_list),
                'employees': employee_list
            })

        except Exception as e:
            _logger.error(f"Error: {str(e)}")
            return self._json_response({'error': str(e), 'status': 500}, 500)

    def _get_or_create_department(self, dept_name):
        if not dept_name:
            return False
        department = request.env['hr.department'].sudo().search([('name', '=', dept_name)], limit=1)
        if not department:
            department = request.env['hr.department'].sudo().create({'name': dept_name})
            _logger.info(f"✨ Created department: {dept_name}")
        return department.id

    def _get_or_create_job(self, job_title):
        if not job_title:
            return False
        job = request.env['hr.job'].sudo().search([('name', '=', job_title)], limit=1)
        if not job:
            job = request.env['hr.job'].sudo().create({'name': job_title})
            _logger.info(f"✨ Created job: {job_title}")
        return job.id

    def _json_response(self, data, status=200):
        return request.make_response(
            json.dumps(data, indent=2),
            headers=[
                ('Content-Type', 'application/json'),
                ('Access-Control-Allow-Origin', '*'),
            ],
            status=status
        )