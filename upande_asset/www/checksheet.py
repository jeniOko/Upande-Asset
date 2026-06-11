import frappe

login_required = True
no_cache = 1


def get_context(context):
    context.title = "Asset Check Sheet"
    context.no_header = 1
    context.no_footer = 1
    context.no_breadcrumbs = 1
    context.company              = frappe.defaults.get_user_default("company") or ""
    context.default_asset_category = frappe.defaults.get_user_default("asset_category") or ""
    context.default_asset          = frappe.defaults.get_user_default("asset") or ""
    context.full_name = frappe.get_value("User", frappe.session.user, "full_name") or ""
