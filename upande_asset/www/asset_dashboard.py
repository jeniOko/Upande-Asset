import frappe

no_cache = 1


def get_context(context):
	if frappe.session.user == "Guest":
		# Send visitors to the login page and bring them back here afterwards.
		frappe.local.flags.redirect_location = "/login?redirect-to=" + frappe.utils.quote("/asset-dashboard")
		raise frappe.Redirect
	context.title = "Asset Dashboard"
	context.no_header = 1
	context.no_footer = 1
	context.no_breadcrumbs = 1
	context.user_id = frappe.session.user
	context.full_name = frappe.get_value("User", frappe.session.user, "full_name") or frappe.session.user
	context.initials = "".join(p[0] for p in context.full_name.split()[:2]).upper() or "U"
	context.default_company = frappe.defaults.get_user_default("company") or ""
	context.asset_version = frappe.utils.cint(frappe.utils.now_datetime().timestamp()) // 600
