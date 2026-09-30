// Кнопка «Сбросить прогресс» на карточке записи на курс (learning-services#313).
// Права проверяет сервер; кнопка лишь не показывается тем, у кого их заведомо нет.
frappe.ui.form.on("LMS Enrollment", {
	refresh(frm) {
		if (frm.is_new()) return;
		const может = frappe.user.has_role(["System Manager", "Administrator", "Moderator", "Course Creator"]);
		if (!может) return;
		frm.add_custom_button(__("Сбросить прогресс"), () => сбросить(frm));
	},
});

function сбросить(frm) {
	frappe.confirm(
		__(
			"Сбросить прогресс {0} по курсу {1}?<br><br>" +
				"Занятия, попытки квиза, документ курса, заметки агента и сдачи домашки по курсу уйдут в архив, " +
				"отметки пройденного и эта запись на курс будут удалены. " +
				"Если курс назначен организацией, запись появится заново — пустая. " +
				"Ответы на репорты ученик по-прежнему увидит.<br><br>Отменить сброс нельзя.",
			[frappe.utils.escape_html(frm.doc.member), frappe.utils.escape_html(frm.doc.course)]
		),
		() =>
			frappe
				.call({
					method: "lms_frappe_app.api.admin.reset_student_progress",
					args: { enrollment: frm.doc.name },
					freeze: true,
					freeze_message: __("Сбрасываем прогресс…"),
				})
				.then((r) => {
					const ответ = r.message || {};
					if (!ответ.ok) {
						frappe.msgprint((ответ.error && ответ.error.message) || __("Не удалось сбросить прогресс"));
						return;
					}
					const д = ответ.data;
					frappe.show_alert(
						{
							message: __(
								"Прогресс сброшен: занятий в архиве — {0}, попыток — {1}, документов — {2}, заметок — {3}, сдач домашки — {4}; отметок пройденного удалено — {5}.",
								[д.sessions_archived, д.attempts_archived, д.artifacts_archived, д.notes_archived, д.homework_archived, д.progress_deleted]
							) + (д.enrolled_again ? " " + __("Курс назначен организацией — запись выдана заново.") : ""),
							indicator: "green",
						},
						10
					);
					frappe.set_route("List", "LMS Enrollment");
				})
	);
}
