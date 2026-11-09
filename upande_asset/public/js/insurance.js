// Global helpers for Asset Insurance (loaded on every desk page).
frappe.provide("upande_asset.insurance");

// Used by server msgprint primary_action (client_action) to jump straight into
// an endorsement dialog on a policy, e.g. from the Asset form.
upande_asset.insurance.open_endorse = function (args) {
	args = typeof args === "string" ? JSON.parse(args) : args || {};
	frappe.route_options = { __endorse: args.action, __asset: args.asset };
	frappe.set_route("Form", "Insurance Policy", args.policy);
};
