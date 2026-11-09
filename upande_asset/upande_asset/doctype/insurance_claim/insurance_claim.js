// Copyright (c) 2026, jeniffer@upande.com and contributors
// For license information, please see license.txt

const CLAIM_API = "upande_asset.upande_asset.doctype.insurance_claim.insurance_claim";

frappe.ui.form.on("Insurance Claim", {
	setup(frm) {
		frm.set_query("asset", (doc) => ({
			filters: { insurance_required: 1, docstatus: 1, ...(doc.company ? { company: doc.company } : {}) },
		}));
		frm.set_query("policy", (doc) => ({ filters: { docstatus: 1, company: doc.company } }));
		frm.set_query("asset_check_sheet", (doc) => ({ filters: { asset: doc.asset, docstatus: 1 } }));
		frm.set_query("asset_repair", (doc) => ({ filters: { asset: doc.asset } }));
		frm.set_query("asset_value_adjustment", (doc) => ({ filters: { asset: doc.asset, docstatus: 1 } }));
		frm.set_query("recoverable_account", (doc) => ({ filters: { company: doc.company, is_group: 0, account_type: "Payable" } }));
		["credit_account", "shortfall_account"].forEach((f) =>
			frm.set_query(f, (doc) => ({ filters: { company: doc.company, is_group: 0 } })));
	},

	asset: (frm) => resolve_policy(frm),
	incident_date: (frm) => resolve_policy(frm),

	incident_type(frm) {
		if (!frm.doc.incident_type) return;
		frappe.xcall(`${CLAIM_API}.get_required_documents`, { incident_type: frm.doc.incident_type }).then((docs) => {
			const have = new Set((frm.doc.documents || []).map((d) => d.document_name));
			(docs || []).filter((d) => !have.has(d.document_name)).forEach((d) => frm.add_child("documents", d));
			frm.refresh_field("documents");
		});
		resolve_policy(frm);
	},

	refresh(frm) {
		if (frm.doc.docstatus !== 1) {
			show_missing_docs(frm);
			return;
		}
		const roles = frappe.user_roles;
		const outstanding = flt(frm.doc.outstanding_amount) > 0;
		const open = ["Approved", "Partly Settled"].includes(frm.doc.status);

		if (open && outstanding && (roles.includes("Accounts User") || roles.includes("Accounts Manager") || roles.includes("System Manager"))) {
			frm.add_custom_button(__("Receive Payment"), () => {
				frappe.xcall(`${CLAIM_API}.make_payment_entry`, { claim: frm.doc.name }).then((doc) => {
					frappe.model.sync(doc);
					frappe.set_route("Form", doc.doctype, doc.name);
				});
			}, __("Create"));
			frm.page.set_inner_btn_group_as_primary(__("Create"));
		}
		if (open && outstanding && (roles.includes("Accounts Manager") || roles.includes("System Manager"))) {
			frm.add_custom_button(__("Close with Shortfall"), () => {
				frappe.prompt(
					{ fieldname: "remarks", fieldtype: "Small Text", label: __("Reason"), reqd: 1 },
					(v) => frappe.confirm(
						__("Write off the outstanding {0} to the claim shortfall account and close this claim?",
							[format_currency(frm.doc.outstanding_amount)]),
						() => frappe.xcall(`${CLAIM_API}.close_with_shortfall`, { claim: frm.doc.name, remarks: v.remarks })
							.then(() => frm.reload_doc())
					),
					__("Close with Shortfall"), __("Continue")
				);
			}, __("Create"));
		}
		["claim_journal_entry", "shortfall_journal_entry"].forEach((f) => {
			if (frm.doc[f]) {
				frm.add_custom_button(__(frappe.unscrub(f)), () => frappe.set_route("Form", "Journal Entry", frm.doc[f]), __("View"));
			}
		});
	},
});

function resolve_policy(frm) {
	if (!frm.doc.asset || !frm.doc.incident_date || frm.doc.docstatus !== 0) return;
	frappe.xcall(`${CLAIM_API}.resolve_policy`, {
		asset: frm.doc.asset, incident_date: frm.doc.incident_date, incident_type: frm.doc.incident_type,
	}).then((policy) => {
		if (policy) {
			if (policy !== frm.doc.policy) frm.set_value("policy", policy);
		} else {
			frm.set_value("policy", "");
			frappe.show_alert({ message: __("No policy covers this asset on the incident date."), indicator: "red" }, 7);
		}
	});
}

function show_missing_docs(frm) {
	const missing = (frm.doc.documents || []).filter((d) => d.mandatory && !d.attachment).map((d) => d.document_name);
	if (missing.length && frm.doc.status === "Draft" && !frm.is_new()) {
		frm.dashboard.set_headline_alert(
			`<span class="indicator orange">${__("Attach before recording")}</span> ${missing.map(frappe.utils.escape_html).join(", ")}`
		);
	}
}
