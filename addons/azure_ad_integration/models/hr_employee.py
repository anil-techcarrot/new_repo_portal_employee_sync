import requests
import json
import logging
from odoo import models, fields, api

_logger = logging.getLogger(__name__)


class HREmployee(models.Model):
    _inherit = 'hr.employee'

    azure_email = fields.Char("Azure Email", readonly=True)
   

    @api.model
    def create(self, vals):
        """Automatically runs when Power Automate creates employee from SharePoint"""
        emp = super(HREmployee, self).create(vals)

        if emp.name:
            emp._create_azure_email()
            
            # This will now work because azure_user_id is saved
            if emp.department_id and emp.azure_user_id:
                emp._add_to_dept_dl()

        return emp

    def _create_azure_email(self):
        """Create unique email in Azure AD"""

        IrConfig = self.env['ir.config_parameter'].sudo()

        tenant_id = IrConfig.get_param("azure_tenant_id")
        client_id = IrConfig.get_param("azure_client_id")
        client_secret = IrConfig.get_param("azure_client_secret")
        domain = IrConfig.get_param("azure_domain")

        if not all([tenant_id, client_id, client_secret, domain]):
            _logger.error("❌ Azure credentials missing in System Parameters!")
            return

        try:
            parts = self.name.strip().lower().split()
            first = parts[0]
            last = parts[-1] if len(parts) > 1 else first
            base = f"{first}.{last}"
            email = f"{base}@{domain}"

            _logger.info(f"🔄 Processing: {self.name} → {email}")

            token_url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
            token_data = {
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": client_secret,
                "scope": "https://graph.microsoft.com/.default"
            }

            token_response = requests.post(token_url, data=token_data, timeout=30)
            token_response.raise_for_status()
            token = token_response.json().get("access_token")

            if not token:
                _logger.error("❌ Failed to get access token")
                return

            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json"
            }

            count = 1
            unique_email = email

            while count < 100:
                check_url = f"https://graph.microsoft.com/v1.0/users/{unique_email}"
                check = requests.get(check_url, headers=headers, timeout=30)

                if check.status_code == 404:
                    _logger.info(f"✅ Email available: {unique_email}")
                    break
                elif check.status_code == 200:
                    count += 1
                    unique_email = f"{base}{count}@{domain}"
                    _logger.info(f"🔄 Email exists, trying: {unique_email}")
                else:
                    _logger.error(f"❌ Error checking email: {check.status_code}")
                    return

            payload = {
                "accountEnabled": True,
                "displayName": self.name,
                "mailNickname": unique_email.split('@')[0],
                "userPrincipalName": unique_email,
                "usageLocation": "AE",
                "passwordProfile": {
                    "forceChangePasswordNextSignIn": True,
                    "password": "Welcome@123"
                }
            }

            create_url = "https://graph.microsoft.com/v1.0/users"
            create_response = requests.post(
                create_url,
                headers=headers,
                data=json.dumps(payload),
                timeout=30
            )

            if create_response.status_code == 201:
                user_data = create_response.json()
                self.azure_email = unique_email
                self.work_email = unique_email
                self.azure_user_id = user_data.get("id")  # ← FIX 2: ADD THIS LINE
                _logger.info(f"✅ SUCCESS! Created: {unique_email} with ID: {self.azure_user_id}")
            else:
                error = create_response.json().get('error', {}).get('message', 'Unknown error')
                _logger.error(f"❌ Failed to create user: {error}")

        except Exception as e:
            _logger.error(f"❌ Exception: {str(e)}")
            
    
