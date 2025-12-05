import requests
import json
from odoo import models, fields, api
import logging

_logger = logging.getLogger(__name__)


class HREmployee(models.Model):
    _inherit = 'hr.employee'

    azure_email = fields.Char("Azure Email", readonly=True)

    @api.model
    def create(self, vals):
        """This triggers automatically when Power Automate creates employee"""
        emp = super().create(vals)
        if emp.name:
            emp._create_azure_email()

            if emp.department_id and emp.azure_user_id:
                emp._add_to_dept_dl()
        return emp

    def _create_azure_email(self):
        """Creates unique email in Azure AD"""

        # Get Azure credentials from Odoo settings
        tenant = self.env['ir.config_parameter'].sudo().get_param("azure_tenant_id")
        client_id = self.env['ir.config_parameter'].sudo().get_param("azure_client_id")
        secret = self.env['ir.config_parameter'].sudo().get_param("azure_client_secret")
        domain = self.env['ir.config_parameter'].sudo().get_param("azure_default_domain")

        if not all([tenant, client_id, secret, domain]):
            _logger.error("Azure credentials missing!")
            return

        try:
            # Generate email from name (e.g., "Lalith kumar" -> lalith.kumar@techcarrot.ae)
            parts = self.name.strip().lower().split()
            first = parts[0]
            last = parts[-1] if len(parts) > 1 else first
            base = f"{first}.{last}"
            email = f"{base}@{domain}"

            _logger.info(f"Generating Azure email for: {self.name}")

            # Get Access Token from Azure
            token_resp = requests.post(
                f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": client_id,
                    "client_secret": secret,
                    "scope": "https://graph.microsoft.com/.default"
                },
                timeout=30
            ).json()

            token = token_resp.get("access_token")
            if not token:
                _logger.error("Failed to get Azure token")
                return

            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json"
            }

            # Check if email exists, make unique (lalith.kumar2, lalith.kumar3, etc.)
            count = 1
            unique_email = email

            while count < 100:
                check = requests.get(
                    f"https://graph.microsoft.com/v1.0/users/{unique_email}",
                    headers=headers,
                    timeout=30
                )

                if check.status_code == 404:
                    # Email doesn't exist - we can use it!
                    break

                # Email exists, try next number
                count += 1
                unique_email = f"{base}{count}@{domain}"

            _logger.info(f"Using unique email: {unique_email}")

            # Create user in Azure AD
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

            res = requests.post(
                "https://graph.microsoft.com/v1.0/users",
                headers=headers,
                data=json.dumps(payload),
                timeout=30
            )

            if res.status_code == 201:
                # Success! Save email to Odoo
                self.azure_email = unique_email
                _logger.info(f"✅ Successfully created Azure email: {unique_email}")
            else:
                error = res.json().get('error', {}).get('message', 'Unknown error')
                _logger.error(f"❌ Failed to create Azure user: {error}")

        except Exception as e:
            _logger.error(f"❌ Exception: {str(e)}")


    def _add_to_dept_dl(self):
        """Add employee to department DL"""
        if not self.department_id or not self.azure_user_id:
            return

        dept = self.department_id

        # Create DL if doesn't exist
        if not dept.azure_dl_id:
            dept.create_dl()

        if dept.azure_dl_id:
            try:
                # Get credentials
                params = self.env['ir.config_parameter'].sudo()
                tenant = params.get_param("azure_tenant_id")
                client = params.get_param("azure_client_id")
                secret = params.get_param("azure_client_secret")

                # Get token
                token_resp = requests.post(
                    f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token",
                    data={
                        "grant_type": "client_credentials",
                        "client_id": client,
                        "client_secret": secret,
                        "scope": "https://graph.microsoft.com/.default"
                    }
                ).json()

                token = token_resp.get("access_token")
                headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

                # Add to DL
                requests.post(
                    f"https://graph.microsoft.com/v1.0/groups/{dept.azure_dl_id}/members/$ref",
                    headers=headers,
                    json={"@odata.id": f"https://graph.microsoft.com/v1.0/users/{self.azure_user_id}"}
                )

                _logger.info(f"✅ Added {self.name} to {dept.azure_dl_email}")
            except Exception as e:
                _logger.error(f"❌ Failed to add to DL: {e}")
