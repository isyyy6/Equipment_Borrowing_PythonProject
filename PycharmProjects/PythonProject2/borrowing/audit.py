from .models import AuditLog


def log_action(actor, action, target, detail=""):
    AuditLog.objects.create(
        actor=actor,
        action=action,
        target_type=target.__class__.__name__,
        target_id=target.pk,
        detail=detail,
    )