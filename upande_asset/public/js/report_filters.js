// Company, Location and accounting dimension filters for every Upande Asset report.
// Call upande_asset.reports.add_scope_filters("<Report Name>") after defining the report.
// Server side: upande_asset.upande_asset.report_utils (group values include their children).
frappe.provide("upande_asset.reports");

upande_asset.reports.add_scope_filters = function (report_name) {
	const report = frappe.query_reports[report_name];
	if (!report) return;
	const filters = report.filters;
	const company_value = () => frappe.query_report && frappe.query_report.get_filter_value("company");

	// Company first (required: amounts are in each company's currency), then Location.
	const existing = filters.findIndex((f) => f.fieldname === "company");
	if (existing > -1) filters.splice(existing, 1);
	filters.unshift(
		{
			fieldname: "company",
			label: __("Company"),
			fieldtype: "Link",
			options: "Company",
			default: frappe.defaults.get_user_default("Company"),
			reqd: 1,
		},
		{
			fieldname: "location",
			label: __("Location"),
			fieldtype: "MultiSelectList",
			get_data: (txt) => frappe.db.get_link_options("Location", txt),
		}
	);

	// Cost Center and the instance's own accounting dimensions (Project left out), after Location.
	// The report page waits for pending calls (frappe.after_ajax) before building its filters.
	frappe.call({
		method: "upande_asset.upande_asset.dashboard.get_filter_options",
		callback(r) {
			const dims = ((r.message || {}).dimensions || []).filter((d) => !filters.some((f) => f.fieldname === d.fieldname));
			const at = filters.findIndex((f) => f.fieldname === "location") + 1;
			filters.splice(at, 0, ...dims.map((d) => ({
				fieldname: d.fieldname,
				label: __(d.label),
				fieldtype: "MultiSelectList",
				get_data: (txt) => frappe.db.get_link_options(d.doctype, txt,
					d.doctype === "Cost Center" && company_value() ? { company: company_value() } : {}),
			})));
		},
	});
};
