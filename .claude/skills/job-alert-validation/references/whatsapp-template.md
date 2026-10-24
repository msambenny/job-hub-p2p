# WhatsApp group message

After validation, format the job with `scripts/format_whatsapp.py` and show the output as copy-ready text. Do not send it through an API. The user pastes it into the WhatsApp group.

Skip empty fields. Use WhatsApp markup: `*bold*` for labels.

```
🏢 *Company:* {company_name}
📋 *Opportunity:* {job_title}
🗂 *Department:* {department}
💼 *Type:* {job_type}
🏠 *Work mode:* {work_mode}
📍 *Location:* {location}

*Who can apply*
🎓 *Education:* {education}
🧑‍💻 *Experience:* {experience}
🛠 *Skills:* {skills}

*About*
{description}

💰 *Salary:* {salary_min} – {salary_max}
🎁 *Benefits:* {benefits}
⏰ *Deadline:* {application_deadline}
📨 *Contact:* {contact_name} · {contact_whatsapp} · {contact_email}
🔗 *Apply:* {apply_link}
📝 *Note:* {custom_note}
```

If salary did not match the source, still include the user's salary line and add a one-line warning above the copy-ready message in chat (not inside the group post unless the user asks).
