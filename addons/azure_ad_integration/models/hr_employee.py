import requests
import json
import logging
from odoo import models, fields, api
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class HREmployee(models.Model):
    _inherit = 'hr.employee'

    azure_email = fields.Char("Azure Email", readonly=True)
    azure_user_id = fields.Char("Azure User ID", readonly=True)
    azure_license_assigned = fields.Boolean("License Assigned", default=False, readonly=True)
    azure_license_name = fields.Char("License Name", readonly=True)

    @api.model_create_multi
    def create(self, vals_list):
        """Automatically runs when employee is created"""
        employees = super().create(vals_list)

        for emp in employees:
            if emp.name:
                # Step 1: Create Azure user
                emp._create_azure_email()

                # Step 2: Assign license automatically
                if emp.azure_user_id:
                    emp._check_and_assign_license()

                # Step 3: Add to department DL automatically
                if emp.department_id and emp.azure_user_id:
                    emp._sync_dept_and_add_to_dl()

        return employees

    def write(self, vals):
        """Monitor department changes and auto-assign to new DL"""
        result = super().write(vals)

        # If department changed, update DL membership automatically
        if 'department_id' in vals:
            for emp in self:
                if emp.azure_user_id and emp.department_id:
                    emp._sync_dept_and_add_to_dl()

        return result

    def _sync_dept_and_add_to_dl(self):
        """Sync department DL if needed, then add employee - FULLY AUTOMATIC"""
        self.ensure_one()

        if not self.department_id or not self.azure_user_id:
            _logger.warning(f"⚠️ Missing dept or user_id for {self.name}")
            return

        dept = self.department_id

        # If department has no DL configured, try to sync it automatically
        if not dept.azure_dl_id:
            _logger.info(f"🔄 Department '{dept.name}' has no DL, attempting auto-sync...")
            # IMPORTANT: Commit the sync result
            sync_result = dept.action_sync_dl_from_azure()

            # Refresh department to get updated values - FIXED LINE
            dept._invalidate_cache(['azure_dl_id', 'azure_dl_email'])

            if not dept.azure_dl_id:
                _logger.warning(f"⚠️ Could not sync DL for department '{dept.name}'")
                return

        # If DL is now configured, add employee
        if dept.azure_dl_id:
            _logger.info(f"✅ Department '{dept.name}' linked to {dept.azure_dl_email}")
            self._add_to_dept_dl()
        else:
            _logger.error(f"❌ Department '{dept.name}' has no DL configured after sync")

    def _create_azure_email(self):
        """Create unique email in Azure AD"""
        self.ensure_one()

        IrConfig = self.env['ir.config_parameter'].sudo()

        tenant_id = IrConfig.get_param("azure_tenant_id")
        client_id = IrConfig.get_param("azure_client_id")
        client_secret = IrConfig.get_param("azure_client_secret")
        domain = IrConfig.get_param("azure_domain")

        if not all([tenant_id, client_id, client_secret, domain]):
            _logger.error("❌ Azure credentials missing in System Parameters!")
            return

        try:
            # Generate email from name
            parts = self.name.strip().lower().split()
            first = parts[0]
            last = parts[-1] if len(parts) > 1 else first
            base = f"{first}.{last}"
            email = f"{base}@{domain}"

            _logger.info(f"🔄 Processing: {self.name} → {email}")

            # Check for duplicate in Odoo first
            existing_emp = self.env['hr.employee'].search([
                ('azure_email', '=', email),
                ('id', '!=', self.id)
            ], limit=1)

            if existing_emp:
                _logger.error(f"❌ DUPLICATE: {email} already assigned to {existing_emp.name}")
                raise UserError(
                    f"Cannot create Azure user!\n\n"
                    f"Email '{email}' is already assigned to '{existing_emp.name}'.\n\n"
                    f"Please use a different name."
                )

            # Get Azure AD token
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

            # Check for unique email in Azure
            count = 1
            unique_email = email

            while count < 100:
                check_url = f"https://graph.microsoft.com/v1.0/users/{unique_email}"
                check = requests.get(check_url, headers=headers, timeout=30)

                if check.status_code == 404:
                    _logger.info(f"✅ Email available: {unique_email}")
                    break
                elif check.status_code == 200:
                    existing_user = check.json()
                    existing_display_name = existing_user.get('displayName')

                    _logger.warning(f"⚠️ {unique_email} exists in Azure ({existing_display_name})")

                    count += 1
                    unique_email = f"{base}{count}@{domain}"
                    _logger.info(f"🔄 Trying: {unique_email}")
                else:
                    _logger.error(f"❌ Error checking email: {check.status_code}")
                    return

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

            create_url = "https://graph.microsoft.com/v1.0/users"
            create_response = requests.post(
                create_url,
                headers=headers,
                data=json.dumps(payload),
                timeout=30
            )

            if create_response.status_code == 201:
                user_data = create_response.json()
                self.write({
                    'azure_email': unique_email,
                    'work_email': unique_email,
                    'azure_user_id': user_data.get("id")
                })
                _logger.info(f"✅ Created: {unique_email} | ID: {self.azure_user_id}")
            else:
                error = create_response.json().get('error', {}).get('message', 'Unknown')
                _logger.error(f"❌ Failed to create user: {error}")

        except UserError:
            raise
        except Exception as e:
            _logger.error(f"❌ Exception: {str(e)}")

    def _check_and_assign_license(self):
        """Check if license already assigned, then assign if needed"""
        self.ensure_one()

        if not self.azure_user_id:
            _logger.error(f"❌ No Azure User ID for {self.name}")
            return False

        params = self.env['ir.config_parameter'].sudo()
        tenant = params.get_param("azure_tenant_id")
        client = params.get_param("azure_client_id")
        secret = params.get_param("azure_client_secret")
        license_sku = params.get_param("azure_license_sku")

        if not license_sku:
            _logger.warning("⚠️ No license SKU configured")
            return False

        try:
            # Get token
            token_resp = requests.post(
                f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": client,
                    "client_secret": secret,
                    "scope": "https://graph.microsoft.com/.default"
                },
                timeout=30
            ).json()

            token = token_resp.get("access_token")
            if not token:
                _logger.error("❌ No token for license check")
                return False

            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json"
            }

            # Check if user already has license in Azure
            check_url = f"https://graph.microsoft.com/v1.0/users/{self.azure_user_id}/licenseDetails"
            check_response = requests.get(check_url, headers=headers, timeout=30)

            if check_response.status_code == 200:
                existing_licenses = check_response.json().get('value', [])
                for lic in existing_licenses:
                    if lic.get('skuId') == license_sku:
                        license_name = lic.get('skuPartNumber', 'Microsoft 365')
                        self.write({
                            'azure_license_assigned': True,
                            'azure_license_name': license_name
                        })
                        _logger.info(f"ℹ️ {self.name} already has license: {license_name}")
                        return True

            # License not found, assign it
            _logger.info(f"🔄 Assigning license to {self.name}...")

            license_payload = {
                "addLicenses": [{
                    "skuId": license_sku,
                    "disabledPlans": []
                }],
                "removeLicenses": []
            }

            license_response = requests.post(
                f"https://graph.microsoft.com/v1.0/users/{self.azure_user_id}/assignLicense",
                headers=headers,
                json=license_payload,
                timeout=30
            )

            if license_response.status_code == 200:
                # Get license name
                sku_response = requests.get(
                    f"https://graph.microsoft.com/v1.0/subscribedSkus",
                    headers=headers,
                    timeout=30
                )

                license_name = "Microsoft 365"
                if sku_response.status_code == 200:
                    skus = sku_response.json().get('value', [])
                    for sku in skus:
                        if sku.get('skuId') == license_sku:
                            license_name = sku.get('skuPartNumber', 'Microsoft 365')
                            break

                self.write({
                    'azure_license_assigned': True,
                    'azure_license_name': license_name
                })
                _logger.info(f"✅ License assigned: {license_name}")
                return True
            else:
                error_data = license_response.json().get('error', {})
                error_msg = error_data.get('message', 'Unknown')

                if 'already' in error_msg.lower():
                    _logger.info(f"ℹ️ License already assigned")
                    self.write({'azure_license_assigned': True})
                    return True

                _logger.error(f"❌ License assignment failed: {error_msg}")
                return False

        except Exception as e:
            _logger.error(f"❌ License check failed: {e}")
            return False

    def _add_to_dept_dl(self):
        """Add employee to department DL - WITH DUPLICATE PREVENTION"""
        self.ensure_one()

        if not self.department_id or not self.azure_user_id:
            _logger.warning(f"⚠️ Missing dept or user_id for {self.name}")
            return

        dept = self.department_id

        if not dept.azure_dl_id:
            _logger.error(f"❌ Department '{dept.name}' has no DL configured")
            return

        _logger.info(f"🔄 Starting DL addition for {self.name}")
        _logger.info(f"   Department: {dept.name}")
        _logger.info(f"   DL Email: {dept.azure_dl_email}")
        _logger.info(f"   DL ID: {dept.azure_dl_id}")
        _logger.info(f"   User ID: {self.azure_user_id}")

        try:
            params = self.env['ir.config_parameter'].sudo()
            tenant = params.get_param("azure_tenant_id")
            client = params.get_param("azure_client_id")
            secret = params.get_param("azure_client_secret")

            token_resp = requests.post(
                f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": client,
                    "client_secret": secret,
                    "scope": "https://graph.microsoft.com/.default"
                },
                timeout=30
            ).json()

            token = token_resp.get("access_token")
            if not token:
                _logger.error("❌ Failed to get token for DL addition")
                return

            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json"
            }

            # Check if already a member
            check_url = f"https://graph.microsoft.com/v1.0/groups/{dept.azure_dl_id}/members/{self.azure_user_id}"
            _logger.info(f"🔍 Checking membership: {check_url}")
            check_response = requests.get(check_url, headers=headers, timeout=30)

            if check_response.status_code == 200:
                _logger.info(f"ℹ️ {self.name} already in {dept.azure_dl_email}")
                return
            elif check_response.status_code == 404:
                _logger.info(f"✅ User not in DL, will add now")
            else:
                _logger.warning(f"⚠️ Unexpected status checking membership: {check_response.status_code}")

            # Not a member, add them
            _logger.info(f"🔄 Adding {self.name} to {dept.azure_dl_email}...")

            add_payload = {"@odata.id": f"https://graph.microsoft.com/v1.0/users/{self.azure_user_id}"}
            add_url = f"https://graph.microsoft.com/v1.0/groups/{dept.azure_dl_id}/members/$ref"

            _logger.info(f"📤 POST {add_url}")
            _logger.info(f"📦 Payload: {json.dumps(add_payload)}")

            add_response = requests.post(
                add_url,
                headers=headers,
                json=add_payload,
                timeout=30
            )

            _logger.info(f"📥 Response Status: {add_response.status_code}")

            if add_response.status_code == 204:
                _logger.info(f"✅ Successfully added {self.name} to {dept.azure_dl_email}")
            elif add_response.status_code == 400:
                error = add_response.json().get('error', {})
                error_msg = error.get('message', 'Unknown')
                if 'already exist' in error_msg.lower():
                    _logger.info(f"ℹ️ {self.name} already in {dept.azure_dl_email}")
                else:
                    _logger.error(f"❌ Failed to add: {error_msg}")
                    _logger.error(f"   Full error: {json.dumps(error)}")
            else:
                _logger.error(f"❌ Failed: HTTP {add_response.status_code}")
                try:
                    error_detail = add_response.json()
                    _logger.error(f"   Error details: {json.dumps(error_detail)}")
                except:
                    _logger.error(f"   Response text: {add_response.text}")

        except Exception as e:
            _logger.error(f"❌ DL addition failed: {e}")
            import traceback
            _logger.error(traceback.format_exc())

    # ... (rest of the methods remain the same)