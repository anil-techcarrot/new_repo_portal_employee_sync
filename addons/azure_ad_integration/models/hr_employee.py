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
            
            if emp.department_id and emp.azure_user_id:
                emp._add_to_dept_dl()
            

        return emp

    def _create_azure_email(self):
        """Create unique email in Azure AD"""

        # ============================================
        # STEP 1: Get credentials from System Parameters
        # ============================================
        IrConfig = self.env['ir.config_parameter'].sudo()

        tenant_id = IrConfig.get_param("azure_tenant_id")
        client_id = IrConfig.get_param("azure_client_id")
        client_secret = IrConfig.get_param("azure_client_secret")
        domain = IrConfig.get_param("azure_domain")

        if not all([tenant_id, client_id, client_secret, domain]):
            _logger.error("❌ Azure credentials missing in System Parameters!")
            return

        try:
            # ============================================
            # STEP 2: Generate email from name
            # ============================================
            # Example: "Lalith Kumar" → "lalith.kumar@techcarrot.ae"
            parts = self.name.strip().lower().split()
            first = parts[0]
            last = parts[-1] if len(parts) > 1 else first
            base = f"{first}.{last}"
            email = f"{base}@{domain}"

            _logger.info(f"🔄 Processing: {self.name} → {email}")

            # ============================================
            # STEP 3: Get Access Token from Azure
            # ============================================
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

            # ============================================
            # STEP 4: Check if email exists, make unique
            # ============================================
            count = 1
            unique_email = email

            while count < 100:
                check_url = f"https://graph.microsoft.com/v1.0/users/{unique_email}"
                check = requests.get(check_url, headers=headers, timeout=30)

                if check.status_code == 404:
                    # Email doesn't exist - we can use it!
                    _logger.info(f"✅ Email available: {unique_email}")
                    break
                elif check.status_code == 200:
                    # Email exists - try next number
                    count += 1
                    unique_email = f"{base}{count}@{domain}"
                    _logger.info(f"🔄 Email exists, trying: {unique_email}")
                else:
                    _logger.error(f"❌ Error checking email: {check.status_code}")
                    return

            # ============================================
            # STEP 5: Create user in Azure AD
            # ============================================
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

            # ============================================
            # STEP 6: Save result
            # ============================================
            if create_response.status_code == 201:
                user_data = create_response.json()
                self.azure_email = unique_email
                self.work_email = unique_email
                _logger.info(f"✅ SUCCESS! Created: {unique_email}")
            else:
                error = create_response.json().get('error', {}).get('message', 'Unknown error')
                _logger.error(f"❌ Failed to create user: {error}")

        except Exception as e:
            _logger.error(f"❌ Exception: {str(e)}")
            
    def _add_to_dept_dl(self):
        """Add employee to department DL"""
        if not self.department_id or not self.azure_user_id:
            return
        
        dept = self.department_id
        
        # Create DL if doesn't exist (only first time)
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
                
                # Add to existing DL
                add_response = requests.post(
                    f"https://graph.microsoft.com/v1.0/groups/{dept.azure_dl_id}/members/$ref",
                    headers=headers,
                    json={"@odata.id": f"https://graph.microsoft.com/v1.0/users/{self.azure_user_id}"}
                )
                
                if add_response.status_code == 204:
                    _logger.info(f"✅ Added {self.name} to {dept.azure_dl_email}")
                elif add_response.status_code == 400:
                    _logger.info(f"ℹ️ {self.name} already in {dept.azure_dl_email}")
                else:
                    _logger.error(f"❌ Failed to add to DL: {add_response.status_code}")
                    
            except Exception as e:
                _logger.error(f"❌ Failed to add to DL: {e}")

