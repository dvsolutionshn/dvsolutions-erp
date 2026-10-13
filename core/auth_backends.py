import logging

from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend
from django.db.models import Q
from django.utils.crypto import salted_hmac


logger = logging.getLogger(__name__)


def _record_login_result(identifier, empresa, result):
    # Permite correlacionar intentos sin escribir correo, usuario ni contraseña.
    identifier_tag = salted_hmac(
        "core.auth.login_diagnostics", identifier.lower(), algorithm="sha256"
    ).hexdigest()[:16]
    logger.info(
        "login_validation result=%s company_id=%s identifier_tag=%s",
        result, getattr(empresa, "pk", None), identifier_tag,
    )


class EmailOrUsernameBackend(ModelBackend):
    def authenticate(self, request, username=None, password=None, **kwargs):
        identifier = (username or kwargs.get("email") or "").strip()
        empresa = kwargs.get("empresa")
        if not identifier or password is None:
            _record_login_result(identifier, empresa, "missing_credentials")
            return None

        UserModel = get_user_model()
        if "@" in identifier:
            users = UserModel._default_manager.filter(email__iexact=identifier)
            if empresa is not None:
                users = users.filter(Q(empresa=empresa) | Q(empresas_acceso=empresa)).distinct()
            match_count = users.count()
            if match_count != 1:
                _record_login_result(
                    identifier, empresa, "not_found" if match_count == 0 else "ambiguous"
                )
                return None
            user = users.first()
        else:
            try:
                user = UserModel._default_manager.get(username__iexact=identifier)
            except UserModel.DoesNotExist:
                _record_login_result(identifier, empresa, "not_found")
                return None

        if not user.check_password(password):
            _record_login_result(identifier, empresa, "invalid_password")
            return None
        if not self.user_can_authenticate(user):
            _record_login_result(identifier, empresa, "inactive")
            return None
        _record_login_result(identifier, empresa, "success")
        return user
