"""Report rendering for app-one (string.Template, so a bad template
placeholder renders literally instead of crashing the app)."""

from string import Template

from common.utils import now_iso


def build_report(config, template_text):
    """Fill the bundled email template from the app's config."""
    values = {
        "greeting": config.get("greeting", "Hello"),
        "app": "app-one",
        "date": now_iso(),
        "status": "OK",
    }
    return Template(template_text or "").safe_substitute(values)
