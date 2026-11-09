import frappe

login_required = True
no_cache = 1


def get_context(context):
	"""Mobile check sheet. Opened plain (scan or search for an asset) or from an asset's QR
	label as /checksheet?asset=<asset number>, which loads that asset straight away."""
	context.title = "Asset Check"
	context.no_header = 1
	context.no_footer = 1
	context.no_breadcrumbs = 1
	context.boot = {
		"today": frappe.utils.today(),
		"csrf": frappe.sessions.get_csrf_token(),
		"user": frappe.utils.get_fullname(frappe.session.user),
	}
