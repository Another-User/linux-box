"""
PromptEngine — Template-based prompt generation for server management tasks.
Uses Jinja2 for flexible, structured prompt rendering.
"""

from __future__ import annotations

import jinja2


_BUILTIN_TEMPLATES: dict[str, str] = {
    "diagnose_issue": """\
You are an expert Linux systems administrator and site reliability engineer.
Analyze the following server issue and provide a structured diagnosis.

## Server Context
- Hostname: {{ hostname }}
- Operating System: {{ os | default('Linux') }}
- Affected Services: {{ affected_services | join(', ') if affected_services else 'Unknown' }}
- Timestamp: {{ timestamp | default('N/A') }}

## Observed Symptoms
{{ symptoms }}

{% if logs %}
## Relevant Log Entries
```
{{ logs }}
```
{% endif %}

{% if metrics %}
## Current System Metrics
{{ metrics }}
{% endif %}

## Instructions
Respond with a JSON block containing:
- `severity`: one of "critical", "high", "medium", "low"
- `root_cause`: concise description of the most likely root cause
- `affected_services`: list of services impacted
- `confidence`: float between 0.0 and 1.0 indicating your confidence
- `explanation`: detailed technical explanation of your diagnosis
- `immediate_actions`: list of immediate steps to take

Then provide a plain-English explanation of the diagnosis for operators.
""",

    "suggest_fix": """\
You are an expert Linux systems administrator. Based on the diagnosis below, \
provide a detailed, safe, and reversible remediation plan.

## Server Context
- Hostname: {{ hostname }}
- Operating System: {{ os | default('Linux') }}
- Current User: {{ current_user | default('root') }}

## Diagnosis
- Severity: {{ severity }}
- Root Cause: {{ root_cause }}
- Affected Services: {{ affected_services | join(', ') if affected_services else 'Unknown' }}

{% if constraints %}
## Constraints
{% for constraint in constraints %}
- {{ constraint }}
{% endfor %}
{% endif %}

## Instructions
Provide an ordered action plan as a JSON array. Each step must include:
- `step`: integer step number
- `description`: what this step does and why
- `command`: the exact shell command to run (or null if no command)
- `expected_output`: what success looks like
- `rollback`: how to undo this step if needed
- `risk`: "safe", "low", "medium", or "high"
- `requires_downtime`: boolean

After the JSON block, include a narrative explanation of the fix strategy and \
any important caveats the operator should be aware of.
""",

    "explain_log": """\
You are an expert Linux systems administrator. Explain the following log output \
in clear, actionable terms for an operations team.

## Server Context
- Hostname: {{ hostname }}
- Service: {{ service | default('Unknown') }}
- Log Source: {{ log_source | default('system log') }}
- Time Range: {{ time_range | default('recent') }}

## Log Content
```
{{ log_content }}
```

{% if context %}
## Additional Context
{{ context }}
{% endif %}

## Instructions
Respond with a JSON block containing:
- `summary`: 1-2 sentence summary of what these logs show
- `details`: detailed technical breakdown of key log entries
- `severity`: overall severity indicated by the logs
- `anomalies`: list of unusual or concerning entries
- `recommendations`: list of recommended follow-up actions

After the JSON block, provide a plain-English explanation suitable for \
both technical and non-technical stakeholders.
""",

    "plan_action": """\
You are an expert Linux systems administrator and automation engineer. \
Create a comprehensive, safe execution plan for the following task.

## Server Context
- Hostname: {{ hostname }}
- Operating System: {{ os | default('Linux') }}
- Environment: {{ environment | default('production') }}
- Available Tools: {{ available_tools | join(', ') if available_tools else 'standard Linux utilities' }}

## Requested Action
{{ action_description }}

{% if current_state %}
## Current System State
{{ current_state }}
{% endif %}

{% if dependencies %}
## Known Dependencies
{% for dep in dependencies %}
- {{ dep }}
{% endfor %}
{% endif %}

## Requirements
- Minimize downtime where possible
- All destructive operations must be preceded by backups or snapshots
- Prefer idempotent commands
- Include validation steps after each major change

## Instructions
Provide an ordered action plan as a JSON array. Each step must include:
- `step`: integer step number
- `phase`: one of "preparation", "execution", "validation", "cleanup"
- `description`: what this step does and why
- `command`: the exact shell command (or null)
- `expected_output`: what success looks like
- `rollback`: how to undo this step
- `risk`: "safe", "low", "medium", or "high"
- `requires_downtime`: boolean
- `estimated_duration_seconds`: rough estimate

Follow the JSON block with a narrative summary of the plan, highlighting \
critical decision points and any assumptions made.
""",

    "summarize_events": """\
You are an expert Linux systems administrator. Summarize the following series \
of system events into a coherent incident or status report.

## Server Context
- Hostname: {{ hostname }}
- Time Window: {{ time_window | default('last hour') }}
- Environment: {{ environment | default('production') }}

## Events
{% for event in events %}
[{{ event.timestamp | default('N/A') }}] [{{ event.level | default('INFO') | upper }}] \
{{ event.source | default('system') }}: {{ event.message }}
{% endfor %}

{% if context %}
## Additional Context
{{ context }}
{% endif %}

## Instructions
Respond with a JSON block containing:
- `incident_detected`: boolean — whether this constitutes an incident
- `overall_severity`: "critical", "high", "medium", "low", or "nominal"
- `timeline`: list of key events in chronological order with their significance
- `patterns`: recurring issues or correlations identified
- `root_causes`: list of identified or suspected root causes
- `impact`: description of service impact and affected users/systems
- `current_status`: "ongoing", "resolved", "monitoring", or "unknown"
- `recommended_actions`: prioritised list of next steps

After the JSON block, write an executive summary suitable for an incident \
report or status page update.
""",
}


class PromptEngine:
    """
    Template-based prompt generation engine for server management LLM tasks.

    Uses Jinja2 for flexible templating. Ships with built-in templates for
    common server operations and supports registration of custom templates.
    """

    def __init__(self) -> None:
        self._jinja_env = jinja2.Environment(
            undefined=jinja2.Undefined,
            trim_blocks=True,
            lstrip_blocks=True,
        )
        self._templates: dict[str, jinja2.Template] = {}
        for name, template_str in _BUILTIN_TEMPLATES.items():
            self._templates[name] = self._jinja_env.from_string(template_str)

    def register_template(self, name: str, template_str: str) -> None:
        """Register or replace a named template.

        Args:
            name: Unique identifier for the template.
            template_str: Jinja2 template source string.
        """
        self._templates[name] = self._jinja_env.from_string(template_str)

    def render(self, template_name: str, **kwargs) -> str:
        """Render a named template with the provided context variables.

        Args:
            template_name: Name of the registered template to render.
            **kwargs: Variables passed to the template.

        Returns:
            Rendered prompt string.

        Raises:
            KeyError: If the template name is not registered.
        """
        if template_name not in self._templates:
            available = ", ".join(sorted(self._templates.keys()))
            raise KeyError(
                f"Template '{template_name}' not found. "
                f"Available templates: {available}"
            )
        return self._templates[template_name].render(**kwargs).strip()

    def render_system_prompt(self, hostname: str, services: list[str]) -> str:
        """Generate a system-level context prompt for LLM conversations.

        This prompt is typically sent as the 'system' role message and primes
        the model with server context before the conversation begins.

        Args:
            hostname: The server's hostname.
            services: List of services currently running on the server.

        Returns:
            System prompt string.
        """
        services_str = "\n".join(f"  - {s}" for s in services) if services else "  (none detected)"
        return (
            f"You are OptAware, an intelligent Linux server management agent "
            f"running on the host '{hostname}'.\n\n"
            f"Your role is to monitor, diagnose, and remediate issues on this server. "
            f"You have deep expertise in Linux systems administration, networking, "
            f"databases, web servers, containerisation, and observability.\n\n"
            f"Currently monitored services:\n{services_str}\n\n"
            f"Guidelines:\n"
            f"  - Always prefer safe, reversible actions over destructive ones.\n"
            f"  - When uncertain, recommend manual review before executing commands.\n"
            f"  - Provide structured JSON output when requested, followed by a "
            f"human-readable explanation.\n"
            f"  - Flag any operation that could cause downtime or data loss as high risk.\n"
            f"  - Reference specific log lines, metrics, or events to support your conclusions.\n"
            f"  - Consider cascading effects before recommending changes to shared services."
        )

    def list_templates(self) -> list[str]:
        """Return the names of all registered templates."""
        return sorted(self._templates.keys())
