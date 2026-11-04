// Interactive enhancements for Workflow Web App

document.addEventListener("DOMContentLoaded", () => {
    // Auto-dismiss alerts after 5 seconds
    const alerts = document.querySelectorAll(".alert");
    alerts.forEach(alert => {
        setTimeout(() => {
            alert.style.transition = "opacity 0.4s ease, transform 0.4s ease";
            alert.style.opacity = "0";
            alert.style.transform = "translateY(-5px)";
            setTimeout(() => alert.remove(), 400);
        }, 5000);
    });

    // Confirmation on destructive actions (e.g. Reject)
    const rejectButtons = document.querySelectorAll("[data-confirm]");
    rejectButtons.forEach(btn => {
        btn.addEventListener("click", (e) => {
            const message = btn.getAttribute("data-confirm") || "Are you sure you want to proceed?";
            if (!confirm(message)) {
                e.preventDefault();
            }
        });
    });

    // Make inputs optional when clicking "Return for Revision" or "Save Draft"
    const skipValidationButtons = document.querySelectorAll(
        'button[value="REQUEST_REVISION"], button[value="REQUEST_CHANGES"], button[value="SAVE_DRAFT"], button[formnovalidate]'
    );

    skipValidationButtons.forEach(btn => {
        btn.addEventListener("click", () => {
            const form = btn.closest("form");
            if (form) {
                const requiredInputs = form.querySelectorAll("input[required], select[required], textarea[required]");
                requiredInputs.forEach(input => {
                    // Keep step_comment / comment required if it is, but make all step form fields optional
                    if (input.name !== "step_comment" && input.name !== "comment") {
                        input.dataset.wasRequired = "true";
                        input.removeAttribute("required");
                    }
                });
            }
        });
    });
});
