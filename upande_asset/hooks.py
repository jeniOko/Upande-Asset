app_name = "upande_asset"
app_title = "Upande Asset"
app_publisher = "jeniffer@upande.com,wycliffe@upande.com"
app_description = "Customizations for the asset module"
app_email = "dev@upande.com"
app_license = "mit"

# Apps
# ------------------

# required_apps = []

# Each item in the list will be shown as an app in the apps page
# add_to_apps_screen = [
# 	{
# 		"name": "upande_asset",
# 		"logo": "/assets/upande_asset/logo.png",
# 		"title": "Upande Asset",
# 		"route": "/upande_asset",
# 		"has_permission": "upande_asset.api.permission.has_app_permission"
# 	}
# ]

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
app_include_css = "/assets/upande_asset/css/upande_asset.css"
app_include_js = [
	"/assets/upande_asset/js/insurance.js",
	"/assets/upande_asset/js/why_why.js",
	"/assets/upande_asset/js/materials.js",
	"/assets/upande_asset/js/report_filters.js",
]

# include js, css files in header of web template
# web_include_css = "/assets/upande_asset/css/upande_asset.css"
# web_include_js = "/assets/upande_asset/js/upande_asset.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "upande_asset/public/scss/website"

# include js, css files in header of web form
# webform_include_js = {"doctype": "public/js/doctype.js"}
# webform_include_css = {"doctype": "public/css/doctype.css"}

# include js in page
# page_js = {"page" : "public/js/file.js"}

# include js in doctype views
doctype_js = {
	"Asset": "public/js/asset.js",
	"Asset Repair": "public/js/asset_repair.js",
}
# doctype_list_js = {"doctype" : "public/js/doctype_list.js"}
# doctype_tree_js = {"doctype" : "public/js/doctype_tree.js"}
# doctype_calendar_js = {"doctype" : "public/js/doctype_calendar.js"}

# Svg Icons
# ------------------
# include app icons in desk
# app_include_icons = "upande_asset/public/icons.svg"

# Home Pages
# ----------

# application home page (will override Website Settings)
# home_page = "login"

# website user home page (by Role)
# role_home_page = {
# 	"Role": "home_page"
# }

# Generators
# ----------

# automatically create page for each record of this doctype
# website_generators = ["Web Page"]

# automatically load and sync documents of this doctype from downstream apps
# importable_doctypes = [doctype_1]

# Jinja
# ----------

# add methods and filters to jinja environment
# jinja = {
# 	"methods": "upande_asset.utils.jinja_methods",
# 	"filters": "upande_asset.utils.jinja_filters"
# }

# Installation
# ------------

# before_install = "upande_asset.install.before_install"
# after_install = "upande_asset.install.after_install"

# Uninstallation
# ------------

# before_uninstall = "upande_asset.uninstall.before_uninstall"
# after_uninstall = "upande_asset.uninstall.after_uninstall"

# Integration Setup
# ------------------
# To set up dependencies/integrations with other apps
# Name of the app being installed is passed as an argument

# before_app_install = "upande_asset.utils.before_app_install"
# after_app_install = "upande_asset.utils.after_app_install"

# Integration Cleanup
# -------------------
# To clean up dependencies/integrations with other apps
# Name of the app being uninstalled is passed as an argument

# before_app_uninstall = "upande_asset.utils.before_app_uninstall"
# after_app_uninstall = "upande_asset.utils.after_app_uninstall"

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "upande_asset.notifications.get_notification_config"

# Permissions
# -----------
# Permissions evaluated in scripted ways

# permission_query_conditions = {
# 	"Event": "frappe.desk.doctype.event.event.get_permission_query_conditions",
# }
#
# has_permission = {
# 	"Event": "frappe.desk.doctype.event.event.has_permission",
# }

# Document Events
# ---------------
# Hook on document methods and events

# doc_events = {
# 	"*": {
# 		"on_update": "method",
# 		"on_cancel": "method",
# 		"on_trash": "method"
# 	}
# }

# Scheduled Tasks
# ---------------

# scheduler_events = {
# 	"all": [
# 		"upande_asset.tasks.all"
# 	],
# 	"daily": [
# 		"upande_asset.tasks.daily"
# 	],
# 	"hourly": [
# 		"upande_asset.tasks.hourly"
# 	],
# 	"weekly": [
# 		"upande_asset.tasks.weekly"
# 	],
# 	"monthly": [
# 		"upande_asset.tasks.monthly"
# 	],
# }

# Testing
# -------

# before_tests = "upande_asset.install.before_tests"

# Extend DocType Class
# ------------------------------
#
# Specify custom mixins to extend the standard doctype controller.
# extend_doctype_class = {
# 	"Task": "upande_asset.custom.task.CustomTaskMixin"
# }

# Overriding Methods
# ------------------------------
#
# override_whitelisted_methods = {
# 	"frappe.desk.doctype.event.event.get_events": "upande_asset.event.get_events"
# }
#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
override_doctype_dashboards = {
	"Asset": "upande_asset.upande_asset.insurance.asset.get_asset_dashboard_data",
	"Asset Repair": "upande_asset.upande_asset.repair_materials.get_dashboard_data",
}

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]

# Ignore links to specified DocTypes when deleting documents
# -----------------------------------------------------------

# ignore_links_on_delete = ["Communication", "ToDo"]

# Request Events
# ----------------
# before_request = ["upande_asset.utils.before_request"]
# after_request = ["upande_asset.utils.after_request"]

# Job Events
# ----------
# before_job = ["upande_asset.utils.before_job"]
# after_job = ["upande_asset.utils.after_job"]

# User Data Protection
# --------------------

# user_data_fields = [
# 	{
# 		"doctype": "{doctype_1}",
# 		"filter_by": "{filter_by}",
# 		"redact_fields": ["{field_1}", "{field_2}"],
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_2}",
# 		"filter_by": "{filter_by}",
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_3}",
# 		"strict": False,
# 	},
# 	{
# 		"doctype": "{doctype_4}"
# 	}
# ]

# Authentication and authorization
# --------------------------------

# auth_hooks = [
# 	"upande_asset.auth.validate"
# ]

# Automatically update python controller files with type annotations for this app.
# export_python_type_annotations = True

# default_log_clearing_doctypes = {
# 	"Logging DocType Name": 30  # days to retain logs
# }

# Translation
# ------------
# List of apps whose translatable strings should be excluded from this app's translations.
# ignore_translatable_strings_from = []

# ============================================================
# ADDITIONS TO: upande_asset/hooks.py
# ============================================================
# Add the sections below into your existing hooks.py file.
# Do NOT replace the whole file — merge these into it.
# ============================================================

# -- Fixtures ----------------------------------------------------
# Installs a custom field on Asset Repair linking it back to the
# originating Asset Check Sheet.

after_install = "upande_asset.upande_asset.install.after_install"
after_migrate = "upande_asset.upande_asset.install.after_migrate"

# -- Doc Events --------------------------------------------------
# These replace the "Asset Repair Status Sync" and
# "Asset Maintenance Status Sync" Server Scripts.
# After adding these and deploying, disable those Server Scripts
# in ERPNext > Server Script list.

doc_events = {
    "Asset Repair": {
        "validate": [
            "upande_asset.upande_asset.rca.asset_repair_validate",
            "upande_asset.upande_asset.repair_materials.asset_repair_validate",
        ],
        "before_submit": "upande_asset.upande_asset.repair_materials.asset_repair_before_submit",
        "onload": "upande_asset.upande_asset.repair_materials.asset_repair_onload",
        "on_update": [
            "upande_asset.upande_asset.doctype.asset_repair_hooks.on_asset_repair_save",
            "upande_asset.upande_asset.rca.on_update",
        ],
        "on_update_after_submit": "upande_asset.upande_asset.rca.on_update",
    },
    "Asset Maintenance Log": {
        "validate": "upande_asset.upande_asset.maintenance.log_validate",
        "after_insert": "upande_asset.upande_asset.maintenance.log_after_insert",
        "on_update": "upande_asset.upande_asset.maintenance.log_on_update",
    },
    "ToDo": {
        "on_update": "upande_asset.upande_asset.rca.todo_on_update",
    },
    "Asset Maintenance": {
        "on_update": [
            "upande_asset.upande_asset.doctype.asset_repair_hooks.on_asset_maintenance_save"
        ],
    },
    # -- Asset Insurance ---------------------------------------------
    "Asset": {
        "on_update_after_submit": "upande_asset.upande_asset.insurance.asset.asset_on_update_after_submit",
    },
    "Purchase Invoice": {
        "validate": "upande_asset.upande_asset.insurance.premium.purchase_invoice_validate",
        "on_submit": [
            "upande_asset.upande_asset.insurance.premium.purchase_invoice_on_submit",
            "upande_asset.upande_asset.doctype.asset_service_record.asset_service_record.purchase_invoice_on_submit",
            "upande_asset.upande_asset.repair_materials.purchase_invoice_on_submit",
        ],
        "on_cancel": [
            "upande_asset.upande_asset.insurance.premium.purchase_invoice_on_cancel",
            "upande_asset.upande_asset.doctype.asset_service_record.asset_service_record.purchase_invoice_on_cancel",
            "upande_asset.upande_asset.repair_materials.purchase_invoice_on_cancel",
        ],
    },
    "Stock Entry": {
        "validate": "upande_asset.upande_asset.repair_materials.stock_entry_validate",
        "on_submit": ["upande_asset.upande_asset.doctype.asset_service_record.asset_service_record.stock_entry_on_change", "upande_asset.upande_asset.repair_materials.stock_entry_on_change"],
        "on_cancel": ["upande_asset.upande_asset.doctype.asset_service_record.asset_service_record.stock_entry_released", "upande_asset.upande_asset.repair_materials.stock_entry_released"],
        "on_trash": ["upande_asset.upande_asset.doctype.asset_service_record.asset_service_record.stock_entry_released", "upande_asset.upande_asset.repair_materials.stock_entry_released"],
    },
    "Material Request": {
        "on_cancel": ["upande_asset.upande_asset.doctype.asset_service_record.asset_service_record.material_request_released", "upande_asset.upande_asset.repair_materials.material_request_released"],
        "on_trash": ["upande_asset.upande_asset.doctype.asset_service_record.asset_service_record.material_request_released", "upande_asset.upande_asset.repair_materials.material_request_released"],
    },
    "Purchase Order": {
        "on_submit": "upande_asset.upande_asset.insurance.premium.purchase_order_on_submit",
    },
    "Payment Entry": {
        "on_submit": "upande_asset.upande_asset.insurance.events.payment_entry_on_change",
        "on_cancel": "upande_asset.upande_asset.insurance.events.payment_entry_on_change",
    },
    "Journal Entry": {
        # Scrapping books an "Asset Disposal" JE (status itself is set via db_set).
        "on_submit": "upande_asset.upande_asset.insurance.asset.journal_entry_on_submit",
    },
    "Sales Invoice": {
        "on_submit": "upande_asset.upande_asset.insurance.asset.sales_invoice_on_submit",
    },
}

# -- Scheduler Events --------------------------------------------
# Replaces the "Asset Check Sheet Missed Check Notifier" Server Script.
# After adding this and deploying, disable that Server Script.

scheduler_events = {
    "daily": [
        "upande_asset.upande_asset.scheduled_tasks.check_missed_asset_checks",
        "upande_asset.upande_asset.insurance.tasks.daily",
    ],
}

# -- Fixtures (Asset Insurance) ----------------------------------
# Core-doctype Custom Fields / Property Setters live in
# upande_asset/upande_asset/custom/*.json (synced on every migrate) and carry
# module = "Upande Asset". This export filter lets `bench export-fixtures`
# capture any further customisations made in the UI for the module.
fixtures = [
    {"dt": "Custom Field", "filters": [["module", "=", "Upande Asset"]]},
    {"dt": "Property Setter", "filters": [["module", "=", "Upande Asset"]]},
]

# ============================================================
# NOTE ON CONTROLLER FILES
# ============================================================
# The AssetCheckSheet and AssetCheckSheetTemplate controller classes
# (asset_check_sheet.py and asset_check_sheet_template.py) are
# picked up automatically by Frappe when placed at:
#
#   upande_asset/upande_asset/doctype/asset_check_sheet/asset_check_sheet.py
#   upande_asset/upande_asset/doctype/asset_check_sheet_template/asset_check_sheet_template.py
#
# No hooks.py entry needed for those — Frappe loads DocType
# controllers by convention from the doctype folder.
#
# After placing the files and running `bench migrate`, disable
# these Server Scripts in the ERPNext UI (don't delete — keep
# them disabled so you have a fallback if needed):
#
#   - Asset Check Sheet Validate
#   - Asset Check Sheet Submit Guard
#   - Asset Repair Status Sync
#   - Asset Maintenance Status Sync
#   - Asset Check Sheet Missed Check Notifier
# ============================================================