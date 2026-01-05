from odoo import http
from odoo.http import request
import json
import logging

_logger = logging.getLogger(__name__)


class PortalEmployeeSyncController(http.Controller):

    def _verify_api_key(self, api_key):
        """Verify API key - Replace with your actual validation logic"""
        # Change this to your actual API key
        valid_key = "d7ce6e48fe7b6dd95283f5c36f6dd791aa83cf65"
        return api_key == valid_key

    @http.route('/api/employees', type='http', auth='public', methods=['POST'], csrf=False, cors='*')
    def create_employee(self, **kwargs):
        """Create employee from external system with all SharePoint fields"""
        try:
            # Get API key from headers
            api_key = request.httprequest.headers.get('api-key') or \
                      request.httprequest.headers.get('API-Key') or \
                      request.httprequest.headers.get('Authorization', '').replace('Bearer ', '')

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

                _logger.info(f"Received employee data: {data}")
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

            # BASE EMPLOYEE DATA (ORIGINAL FIELDS - KEPT AS IS)
            employee_vals = {
                'name': data.get('name'),
                'work_email': data.get('email'),
                'mobile_phone': data.get('phone'),
                'department_id': self._get_or_create_department(data.get('department')),
                'job_id': self._get_or_create_job(data.get('job_title')),
            }

            # NEW SHAREPOINT FIELDS - ONLY ADD IF PROVIDED
            if data.get('employee_first_name'):
                employee_vals['employee_first_name'] = data.get('employee_first_name')

            if data.get('employee_middle_name'):
                employee_vals['employee_middle_name'] = data.get('employee_middle_name')

            if data.get('employee_last_name'):
                employee_vals['employee_last_name'] = data.get('employee_last_name')

            # Gender (values: 'male', 'female', 'other')
            if data.get('sex'):
                gender_value = data.get('sex').lower()
                if gender_value in ['male', 'female', 'other']:
                    employee_vals['gender'] = gender_value

            # Birthday (format: MM/DD/YYYY or YYYY-MM-DD)
            if data.get('birthday'):
                try:
                    from datetime import datetime
                    birthday_str = data.get('birthday')

                    # Try MM/DD/YYYY format first
                    try:
                        date_obj = datetime.strptime(birthday_str, '%m/%d/%Y')
                    except:
                        # Try YYYY-MM-DD format
                        date_obj = datetime.strptime(birthday_str, '%Y-%m-%d')

                    employee_vals['birthday'] = date_obj.strftime('%Y-%m-%d')
                except Exception as e:
                    _logger.warning(f"Invalid date format for birthday: {birthday_str}")

            if data.get('place_of_birth'):
                employee_vals['place_of_birth'] = data.get('place_of_birth')

            # Marital status (values: 'single', 'married', 'cohabitant', 'widower', 'divorced')
            if data.get('marital'):
                marital_value = data.get('marital').lower()
                if marital_value in ['single', 'married', 'cohabitant', 'widower', 'divorced']:
                    employee_vals['marital'] = marital_value

            if data.get('private_email'):
                employee_vals['private_email'] = data.get('private_email')

            # Nationality (country_id)
            if data.get('country_id'):
                country = request.env['res.country'].sudo().search([
                    '|',
                    ('name', 'ilike', data.get('country_id')),
                    ('code', '=', data.get('country_id').upper())
                ], limit=1)
                if country:
                    employee_vals['country_id'] = country.id

            # Mother tongue
            if data.get('mother_tongue_id'):
                lang = request.env['res.lang'].sudo().search([
                    '|',
                    ('name', 'ilike', data.get('mother_tongue_id')),
                    ('iso_code', 'ilike', data.get('mother_tongue_id'))
                ], limit=1)
                if lang:
                    employee_vals['mother_tongue_id'] = lang.id

            # Languages known (comma-separated string)
            if data.get('language_known_ids'):
                try:
                    lang_names = [l.strip() for l in data.get('language_known_ids').split(',') if l.strip()]
                    if lang_names:
                        langs = request.env['res.lang'].sudo().search([
                            ('name', 'in', lang_names)
                        ])
                        if langs:
                            employee_vals['language_known_ids'] = [(6, 0, langs.ids)]
                except Exception as e:
                    _logger.warning(f"Error processing languages: {e}")

            # CREATE EMPLOYEE
            employee = request.env['hr.employee'].sudo().create(employee_vals)

            _logger.info(f"Employee created successfully: {employee.name} (ID: {employee.id})")

            # RETURN RESPONSE (ORIGINAL FORMAT + NEW FIELDS)
            return self._json_response({
                'success': True,
                'status': 'success',
                'employee_id': employee.id,
                'message': 'Employee created successfully',
                'data': {
                    'name': employee.name,
                    'email': employee.work_email,
                    'phone': employee.mobile_phone,
                    'first_name': employee.employee_first_name or '',
                    'middle_name': employee.employee_middle_name or '',
                    'last_name': employee.employee_last_name or '',
                    'department': employee.department_id.name if employee.department_id else '',
                    'gender': employee.gender or '',
                    'birthday': employee.birthday.strftime('%Y-%m-%d') if employee.birthday else '',
                }
            })

        except Exception as e:
            _logger.error(f"Error creating employee: {str(e)}", exc_info=True)
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
                    'department': emp.department_id.name if emp.department_id else '',
                    'job_title': emp.job_id.name if emp.job_id else '',
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
            json.dumps(data),
            headers=[
                ('Content-Type', 'application/json'),
                ('Access-Control-Allow-Origin', '*'),
            ],
            status=status
        )
