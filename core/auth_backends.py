from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend


class EmailOrUsernameBackend(ModelBackend):
    def authenticate(self, request, username=None, password=None, **kwargs):
        identifier = (username or kwargs.get("email") or "").strip()
        empresa = kwargs.get("empresa")
        if not identifier or password is None:
            return None

        UserModel = get_user_model()
        if "@" in identifier:
            users = UserModel._default_manager.filter(email__iexact=identifier)
            if empresa is not None:
                users = users.filter(empresa=empresa)
            if users.count() != 1:
                return None
            user = users.first()
        else:
            try:
                user = UserModel._default_manager.get(username__iexact=identifier)
            except UserModel.DoesNotExist:
                return None

        if user.check_password(password) and self.user_can_authenticate(user):
            return user
        return None
