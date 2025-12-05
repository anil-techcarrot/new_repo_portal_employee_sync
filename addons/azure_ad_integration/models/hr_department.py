import requests
import logging
from odoo import models, fields

_logger = logging.getLogger(__name__)


class HRDepartment(models.Model):
    _inherit = 'hr.department'

    azure_dl_email = fields.Char("DL Email", readonly=True)
    azure_dl_id = fields.Char("DL ID", readonly=True)

    def create_dl(self):
        """Create DL for department - ONLY ONCE"""
        if self.azure_dl_email:
            _logger.info(f"DL already exists: {self.azure_dl_email}")
            return  # ← STOPS HERE IF DL EXISTS!

        # Get credentials
        params = self.env['ir.config_parameter'].sudo()
        tenant = params.get_param("azure_tenant_id")
        client = params.get_param("azure_client_id")
        secret = params.get_param("azure_client_secret")
        domain = params.get_param("azure_domain")

        try:
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

            # Create DL
            dept_name = self.name.replace(' ', '_')
            dl_email = f"DL_{dept_name}@{domain}"

            _logger.info(f"Creating DL: {dl_email}")

            response = requests.post(
                "https://graph.microsoft.com/v1.0/groups",
                headers=headers,
                json={
                    "displayName": f"DL {self.name}",
                    "mailNickname": f"DL_{dept_name}",
                    "mailEnabled": True,
                    "securityEnabled": False,
                    "groupTypes": ["Unified"]
                }
            )

            if response.status_code == 201:
                data = response.json()
                self.write({
                    'azure_dl_email': dl_email,
                    'azure_dl_id': data.get("id")
                })
                _logger.info(f"✅ Created DL: {dl_email}")
            else:
                error = response.json().get('error', {}).get('message', 'Unknown')
                _logger.error(f"❌ Failed to create DL: {error}")

        except Exception as e:
            _logger.error(f"❌ DL creation failed: {e}")



