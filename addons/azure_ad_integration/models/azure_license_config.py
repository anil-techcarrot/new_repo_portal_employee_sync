import requests
import logging
from odoo import models, fields, api

_logger = logging.getLogger(__name__)


class AzureLicenseConfig(models.Model):
    _name = 'azure.license.config'
    _description = 'Azure License Configuration'
    _rec_name = 'license_name'

    license_name = fields.Char("License Name", readonly=True)
    license_sku = fields.Char("License SKU", readonly=True)
    total_licenses = fields.Integer("Total Licenses", readonly=True)
    assigned_licenses = fields.Integer("Assigned Licenses", readonly=True)
    available_licenses = fields.Integer("Available Licenses", compute='_compute_available', store=True)
    last_sync = fields.Datetime("Last Synced", readonly=True)

    @api.depends('total_licenses', 'assigned_licenses')
    def _compute_available(self):
        for record in self:
            record.available_licenses = record.total_licenses - record.assigned_licenses

    def action_sync_licenses_from_azure(self):
        """Fetch license info from Azure - works from any record or list view"""
        params = self.env['ir.config_parameter'].sudo()
        tenant = params.get_param("azure_tenant_id")
        client = params.get_param("azure_client_id")
        secret = params.get_param("azure_client_secret")

        if not all([tenant, client, secret]):
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'message': '⚠️ Azure credentials missing in System Parameters',
                    'type': 'danger',
                    'sticky': True
                }
            }

        try:
            _logger.info("=" * 80)
            _logger.info("🔄 Starting Azure license sync...")

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
            )

            if token_resp.status_code != 200:
                _logger.error(f"❌ Token request failed: {token_resp.status_code}")
                _logger.error(f"   Response: {token_resp.text}")
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'message': f'Failed to authenticate with Azure: {token_resp.status_code}',
                        'type': 'danger',
                    }
                }

            token = token_resp.json().get("access_token")
            if not token:
                _logger.error("❌ No token in response")
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'message': 'Failed to get access token from Azure',
                        'type': 'danger',
                    }
                }

            headers = {"Authorization": f"Bearer {token}"}

            # Get all subscribed SKUs
            _logger.info("📡 Fetching licenses from Azure...")
            response = requests.get(
                "https://graph.microsoft.com/v1.0/subscribedSkus",
                headers=headers,
                timeout=30
            )

            _logger.info(f"   Response status: {response.status_code}")

            if response.status_code == 200:
                skus = response.json().get('value', [])
                _logger.info(f"   Found {len(skus)} license types in Azure")

                if not skus:
                    _logger.warning("⚠️ No SKUs returned from Azure")
                    return {
                        'type': 'ir.actions.client',
                        'tag': 'display_notification',
                        'params': {
                            'message': 'No licenses found in Azure tenant',
                            'type': 'warning',
                        }
                    }

                # Clear old records
                old_records = self.env['azure.license.config'].search([])
                _logger.info(f"🗑️ Deleting {len(old_records)} old records...")
                old_records.unlink()

                # Create new records for each license
                created_count = 0
                for sku in skus:
                    license_name = sku.get('skuPartNumber', 'Unknown')
                    sku_id = sku.get('skuId')
                    total = sku.get('prepaidUnits', {}).get('enabled', 0)
                    consumed = sku.get('consumedUnits', 0)

                    _logger.info(f"   📦 {license_name}: {consumed}/{total} assigned")

                    self.env['azure.license.config'].create({
                        'license_name': license_name,
                        'license_sku': sku_id,
                        'total_licenses': total,
                        'assigned_licenses': consumed,
                        'last_sync': fields.Datetime.now()
                    })
                    created_count += 1

                _logger.info(f"✅ Successfully synced {created_count} license types")
                _logger.info("=" * 80)

                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'message': f'✅ Successfully synced {created_count} license types from Azure',
                        'type': 'success',
                        'sticky': False
                    }
                }
            else:
                error_msg = response.text
                _logger.error(f"❌ Failed to get licenses: {response.status_code}")
                _logger.error(f"   Error: {error_msg}")

                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'message': f'Failed to sync licenses: HTTP {response.status_code}',
                        'type': 'danger',
                    }
                }

        except requests.exceptions.Timeout:
            _logger.error("❌ Request timeout")
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'message': 'Request timeout - Azure API did not respond',
                    'type': 'danger',
                }
            }
        except Exception as e:
            _logger.error(f"❌ Exception: {e}")
            import traceback
            _logger.error(traceback.format_exc())

            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'message': f'Error syncing licenses: {str(e)}',
                    'type': 'danger',
                }
            }