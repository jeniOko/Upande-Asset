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
# app_include_js = "/assets/upande_asset/js/upande_asset.js"

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
# doctype_js = {"doctype" : "public/js/doctype.js"}
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
# override_doctype_dashboards = {
# 	"Task": "upande_asset.task.get_dashboard_data"
# }

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
        "on_update": [
            "upande_asset.upande_asset.doctype.asset_repair_hooks.on_asset_repair_save"
        ],
    },
    "Asset Maintenance": {
        "on_update": [
            "upande_asset.upande_asset.doctype.asset_repair_hooks.on_asset_maintenance_save"
        ],
    },
}

# -- Scheduler Events --------------------------------------------
# Replaces the "Asset Check Sheet Missed Check Notifier" Server Script.
# After adding this and deploying, disable that Server Script.

scheduler_events = {
    "daily": [
        "upande_asset.upande_asset.scheduled_tasks.check_missed_asset_checks"
    ],
}

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