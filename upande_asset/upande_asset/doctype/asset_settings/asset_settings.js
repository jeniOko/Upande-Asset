// Copyright (c) 2026, jeniffer@upande.com and contributors
// For license information, please see license.txt

const ACCOUNT_FILTERS = {
	prepaid_insurance_account: { root_type: "Asset" },
	insurance_expense_account: { root_type: "Expense" },
	claim_recoverable_account: { root_type: "Liability", account_type: "Payable" },
	claim_recovery_income_account: { root_type: "Income" },
	claim_shortfall_account: { root_type: "Expense" },
};

frappe.ui.form.on("Asset Settings", {
	setup(frm) {
		Object.entries(ACCOUNT_FILTERS).forEach(([field, filters]) => {
			frm.set_query(field, "company_defaults", (doc, cdt, cdn) => ({
				filters: { ...filters, is_group: 0, company: locals[cdt][cdn].company },
			}));
		});
		["parts_warehouse", "insurance_request_warehouse"].forEach((field) =>
			frm.set_query(field, "company_defaults", (doc, cdt, cdn) => ({
				filters: { is_group: 0, company: locals[cdt][cdn].company },
			})));
		frm.set_query("premium_item", () => ({ filters: { is_stock_item: 0, is_fixed_asset: 0, disabled: 0 } }));
	},
});
