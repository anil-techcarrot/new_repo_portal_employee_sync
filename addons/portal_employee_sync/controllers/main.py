from odoo import http
from odoo.http import request
import json
import logging

_logger = logging.getLogger(__name__)


class PortalEmployeeController(http.Controller):
    VALID_API_KEY = '44e556b9417e61c1952abfdfb264bf38fa4ee96f'

    @http.route('/api/employees',
                type='http',
                auth='public',
                methods=['POST'],
                csrf=False,
                cors='*')
    def create_employee(self, **kwargs):
        """Create employee from portal"""
        try:
            _logger.info("=" * 70)
            _logger.info(" API: Create Employee")

            # Validate API Key
            api_key = request.httprequest.headers.get('API-Key')
            if not api_key or api_key != self.VALID_API_KEY:
                _logger.warning("Invalid API Key")
                return self._json_response({'error': 'Invalid API Key', 'status': 401}, 401)

            # Parse data
            try:
                if request.httprequest.data:
                    data = json.loads(request.httprequest.data.decode('utf-8'))
                else:
                    data = request.httprequest.form.to_dict()
                _logger.info(f" Data: {data}")
            except Exception as e:
                return self._json_response({'error': f'Invalid JSON: {str(e)}', 'status': 400}, 400)

            # Validate required fields
            if not data.get('name'):
                return self._json_response({'error': 'Name required', 'status': 400}, 400)
            if not data.get('email'):
                return self._json_response({'error': 'Email required', 'status': 400}, 400)

            # Get/create department
            department_id = False
            if data.get('department'):
                department_id = self._get_or_create_department(data['department'])

            # Prepare employee data
            employee_vals = {
                'name': data['name'],
                'work_email': data['email'],
                'work_phone': data.get('phone', ''),
                'department_id': department_id,
            }

            if 'employee_code' in request.env['hr.employee']._fields:
                employee_vals['employee_code'] = data.get('employee_code', '')

            if 'portal_sync_date' in request.env['hr.employee']._fields:
                from odoo import fields
                employee_vals['portal_sync_date'] = fields.Datetime.now()

            # Create employee
            employee = request.env['hr.employee'].sudo().create(employee_vals)

            _logger.info("=" * 70)
            _logger.info(f"CREATED: {employee.name} (ID: {employee.id})")
            _logger.info("=" * 70)

            return self._json_response({
                'success': True,
                'id': employee.id,
                'name': employee.name,
                'email': employee.work_email,
                'status': 200
            })

        except Exception as e:
            _logger.error(f"Error: {str(e)}", exc_info=True)
            return self._json_response({'error': str(e), 'status': 500}, 500)

    def _get_or_create_department(self, department_name):
        """Get or create department"""
        if not department_name:
            return False

        department = request.env['hr.department'].sudo().search([
            ('name', '=', department_name)
        ], limit=1)

        if not department:
            department = request.env['hr.department'].sudo().create({
                'name': department_name
            })
            _logger.info(f" Created department: {department_name}")

        return department.id

    def _json_response(self, data, status=200):
        """Return JSON response"""
        return request.make_response(
            json.dumps(data),
            headers=[
                ('Content-Type', 'application/json'),
                ('Access-Control-Allow-Origin', '*'),
            ],
            status=status
        )

    @http.route('/api/employees/test',
                type='http',
                auth='public',
                methods=['GET'],
                csrf=False)
    def test_endpoint(self, **kwargs):
        """Test API endpoint"""
        return self._json_response({
            'status': 'OK',
            'message': 'API is working!',
            'endpoint': '/api/employees',
            'method': 'POST',
            'required_fields': ['name', 'email']
        })
