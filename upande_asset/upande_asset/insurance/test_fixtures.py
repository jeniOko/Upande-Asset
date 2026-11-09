# Copyright (c) 2026, jeniffer@upande.com and contributors
# For license information, please see license.txt
"""Idempotent fixtures shared by the Asset Insurance tests.

Creates its own company (`_Test Insurance Co`) with the five insurance accounts,
two cost centers, an insurer, a premium item and a tractor asset category so
the tests do not depend on ERPNext's full test bootstrap."""

import frappe
from frappe.utils import add_days, add_years, getdate, today

COMPANY = "_Test Insurance Co"
ABBR = "_TIC"
INSURER = "_Test Insurer"
BROKER = "_Test Insurance Broker"
PREMIUM_ITEM = "_Test Insurance Premium"
ASSET_ITEM = "_Test Insured Tractor"
CATEGORY = "_Test Insured Tractors"
LOCATION = "_Test Insurance Yard"
CC_A = f"_Test Farm A - {ABBR}"
CC_B = f"_Test Farm B - {ABBR}"


def acc(name):
	return f"{name} - {ABBR}"


def _group(root_type, account_type=None):
	filters = {"company": COMPANY, "is_group": 1, "root_type": root_type}
	name = None
	if account_type:
		name = frappe.db.get_value("Account", {**filters, "is_group": 0, "account_type": account_type}, "parent_account")
	return name or frappe.db.get_value("Account", filters, "name", order_by="lft desc")


def _account(name, root_type, account_type=None, parent=None):
	full = acc(name)
	if not frappe.db.exists("Account", full):
		frappe.get_doc({
			"doctype": "Account",
			"account_name": name,
			"company": COMPANY,
			"parent_account": parent or _group(root_type, account_type),
			"account_type": account_type,
			"is_group": 0,
		}).insert(ignore_permissions=True)
	return full


def _ensure(doctype, name, values):
	if not frappe.db.exists(doctype, name):
		frappe.get_doc({"doctype": doctype, **values}).insert(ignore_permissions=True)
	return name


def bootstrap():
	frappe.set_user("Administrator")
	if not frappe.db.exists("Item Group", "All Item Groups"):
		from erpnext.setup.setup_wizard.operations.install_fixtures import install

		install("Kenya")
	if not frappe.db.exists("Company", COMPANY):
		frappe.get_doc({
			"doctype": "Company",
			"company_name": COMPANY,
			"abbr": ABBR,
			"default_currency": "KES",
			"country": "Kenya",
			"create_chart_of_accounts_based_on": "Standard Template",
			"chart_of_accounts": "Standard",
		}).insert(ignore_permissions=True)
	if not frappe.db.get_single_value("System Settings", "setup_complete"):
		frappe.db.set_single_value("System Settings", "setup_complete", 1)
	for year in range(getdate(today()).year - 2, getdate(today()).year + 3):
		name = str(year)
		if not frappe.db.exists("Fiscal Year", name):
			frappe.get_doc({
				"doctype": "Fiscal Year",
				"year": name,
				"year_start_date": f"{year}-01-01",
				"year_end_date": f"{year}-12-31",
			}).insert(ignore_permissions=True)

	main_cc = frappe.get_cached_value("Company", COMPANY, "cost_center")
	parent_cc = frappe.db.get_value("Cost Center", {"company": COMPANY, "is_group": 1}, "name")
	for cc in (CC_A, CC_B):
		_ensure("Cost Center", cc, {"cost_center_name": cc.replace(f" - {ABBR}", ""), "company": COMPANY,
			"parent_cost_center": parent_cc, "is_group": 0})

	accounts = {
		"prepaid_insurance_account": _account("Prepaid Insurance", "Asset", parent=_group("Asset", "Receivable")),
		"insurance_expense_account": _account("Insurance Expense", "Expense"),
		"claim_recoverable_account": _account("Insurance Claims Recoverable", "Liability", "Payable"),
		"claim_recovery_income_account": _account("Insurance Claim Recoveries", "Income"),
		"claim_shortfall_account": _account("Insurance Claim Shortfall", "Expense"),
	}
	company = frappe.get_doc("Company", COMPANY)
	if not company.depreciation_cost_center or not company.disposal_account:
		company.depreciation_cost_center = company.depreciation_cost_center or main_cc
		company.disposal_account = company.disposal_account or _account("Gain/Loss on Asset Disposal", "Expense")
		company.save(ignore_permissions=True)

	if not frappe.db.exists("Supplier Group", "Insurers"):
		from upande_asset.upande_asset.insurance.setup import ensure_supplier_group

		ensure_supplier_group()
	for sup in (INSURER, BROKER):
		_ensure("Supplier", sup, {"supplier_name": sup, "supplier_group": "Insurers", "email_id": "broker@example.com" if sup == BROKER else None})

	_ensure("Location", LOCATION, {"location_name": LOCATION})
	_ensure("Item", PREMIUM_ITEM, {"item_code": PREMIUM_ITEM, "item_name": "Insurance premium", "item_group": "Services",
		"stock_uom": "Nos", "is_stock_item": 0, "enable_deferred_expense": 1, "no_of_months_exp": 12})

	fixed_asset = _account("Tractors", "Asset", "Fixed Asset")
	acc_dep = _account("Accumulated Depreciation Tractors", "Asset", "Accumulated Depreciation")
	dep = _account("Depreciation Tractors", "Expense", "Depreciation")
	cwip = _account("CWIP Tractors", "Asset", "Capital Work in Progress")
	if not frappe.db.exists("Asset Category", CATEGORY):
		frappe.get_doc({
			"doctype": "Asset Category",
			"asset_category_name": CATEGORY,
			"insurance_required": 1,
			"accounts": [{
				"company_name": COMPANY,
				"fixed_asset_account": fixed_asset,
				"accumulated_depreciation_account": acc_dep,
				"depreciation_expense_account": dep,
				"capital_work_in_progress_account": cwip,
			}],
		}).insert(ignore_permissions=True)
	_ensure("Item", ASSET_ITEM, {"item_code": ASSET_ITEM, "item_name": "Tractor", "item_group": "All Item Groups",
		"stock_uom": "Nos", "is_stock_item": 0, "is_fixed_asset": 1, "asset_category": CATEGORY})

	from upande_asset.upande_asset.insurance.setup import ensure_incident_types, ensure_policy_types

	ensure_policy_types()
	ensure_incident_types()
	settings = frappe.get_single("Asset Settings")
	settings.premium_item = PREMIUM_ITEM
	settings.renewal_alert_days = "60,30,7"
	settings.expiring_threshold_days = 30
	settings.claim_followup_days = 14
	settings.renewal_procurement = "Material Request"
	settings.endorsements_via_po = 0
	settings.auto_create_service_records = 1
	settings.material_request_status = "Draft"
	settings.maintenance_mr_status = "Draft"
	settings.purchase_order_status = "Draft"
	settings.split_premium_by_cost_center = 1
	row = next((r for r in settings.company_defaults if r.company == COMPANY), None) or settings.append("company_defaults", {"company": COMPANY})
	row.update(accounts)
	settings.claim_required_documents = []
	settings.append("claim_required_documents", {"incident_type": "", "document_name": "Claim form", "mandatory": 1})
	settings.append("claim_required_documents", {"incident_type": "Theft", "document_name": "Police abstract", "mandatory": 1})
	settings.save(ignore_permissions=True)
	frappe.db.commit()
	return frappe._dict(accounts)


def make_asset(cost_center=CC_A, value=1_000_000, insurance_required=1, submit=True):
	purchase = add_years(today(), -1)
	asset = frappe.get_doc({
		"doctype": "Asset",
		"company": COMPANY,
		"item_code": ASSET_ITEM,
		"asset_name": f"Tractor {frappe.generate_hash(length=5)}",
		"asset_category": CATEGORY,
		"location": LOCATION,
		"cost_center": cost_center,
		"is_existing_asset": 1,
		"calculate_depreciation": 0,
		"purchase_date": purchase,
		"available_for_use_date": purchase,
		"net_purchase_amount": value,
		"purchase_amount": value,
		"gross_purchase_amount": value,
		"asset_quantity": 1,
		"insurance_required": insurance_required,
	})
	asset.insert(ignore_permissions=True)
	if submit:
		asset.submit()
	return asset


def make_policy(assets, start=None, end=None, submit=True, policy_type="Comprehensive", basic=120_000, levies=1_200,
		sums=None, **extra):
	from upande_asset.upande_asset.insurance.setup import ensure_policy_type

	ensure_policy_type(policy_type)
	start = getdate(start or add_days(today(), -30))
	end = getdate(end or add_days(add_years(start, 1), -1))
	policy = frappe.get_doc({
		"doctype": "Insurance Policy",
		"company": COMPANY,
		"insurer": INSURER,
		"broker": BROKER,
		"policy_number": f"POL-{frappe.generate_hash(length=6)}",
		"policy_type": policy_type,
		"start_date": start,
		"end_date": end,
		"basic_premium": basic,
		"levies": levies,
		"excess_type": "Fixed",
		"excess_value": 25_000,
		"insured_assets": [
			{"asset": a.name, "sum_insured": (sums or {}).get(a.name, 1_000_000)} for a in assets
		],
		**extra,
	})
	policy.insert(ignore_permissions=True)
	if submit:
		policy.submit()
	return policy


def set_parts_warehouse(warehouse, company=COMPANY):
	"""Workshop parts warehouse on the company's row in Asset Settings → Defaults."""
	frappe.db.set_value("Asset Company Default", {"parent": "Asset Settings", "company": company}, "parts_warehouse", warehouse)
	frappe.clear_document_cache("Asset Settings", "Asset Settings")
