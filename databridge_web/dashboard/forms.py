from __future__ import annotations

from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password


PUBLISH_MODES = (
    ("new", "Nouveau dataset"),
    ("version", "Nouvelle version"),
)


class DatasetCreateForm(forms.Form):
    mode = forms.ChoiceField(choices=PUBLISH_MODES)
    existing_slug = forms.CharField(required=False)
    source_code = forms.CharField()
    topic_id = forms.IntegerField(
        required=True,
        error_messages={"required": "Veuillez sélectionner un thème avant de continuer."},
    )
    indicator_ids = forms.MultipleChoiceField(required=False)
    country_id = forms.IntegerField()
    start_date = forms.DateField(input_formats=["%Y-%m-%d"])
    end_date = forms.DateField(input_formats=["%Y-%m-%d"])
    title = forms.CharField(max_length=180)
    description = forms.CharField()

    def __init__(self, *args, indicator_choices=None, max_indicators=None, source_label=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["indicator_ids"].choices = indicator_choices or []
        self.max_indicators = max_indicators
        self.source_label = source_label or "La source"

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("mode") == "version" and not cleaned.get("existing_slug"):
            self.add_error("existing_slug", "Sélectionnez le dataset à versionner.")
        if not cleaned.get("indicator_ids"):
            self.add_error("indicator_ids", "Sélectionnez au moins un indicateur.")
        elif self.max_indicators and len(cleaned.get("indicator_ids", [])) > self.max_indicators:
            self.add_error(
                "indicator_ids",
                f"{self.source_label} autorise au maximum {self.max_indicators} indicateurs par dataset.",
            )
        start_date = cleaned.get("start_date")
        end_date = cleaned.get("end_date")
        if start_date and end_date and start_date > end_date:
            self.add_error("end_date", "La date de fin doit être supérieure ou égale à la date de début.")
        return cleaned


class SuperuserCreationForm(forms.Form):
    username = forms.CharField(
        label="Nom d'utilisateur",
        max_length=150,
        widget=forms.TextInput(attrs={"class": "form-control", "autocomplete": "username"}),
    )
    email = forms.EmailField(
        label="Email",
        required=False,
        widget=forms.EmailInput(attrs={"class": "form-control", "autocomplete": "email"}),
    )
    password1 = forms.CharField(
        label="Mot de passe",
        widget=forms.PasswordInput(attrs={"class": "form-control", "autocomplete": "new-password"}),
    )
    password2 = forms.CharField(
        label="Confirmation du mot de passe",
        widget=forms.PasswordInput(attrs={"class": "form-control", "autocomplete": "new-password"}),
    )

    def clean_username(self):
        username = self.cleaned_data["username"].strip()
        User = get_user_model()
        if User.objects.filter(username__iexact=username).exists():
            raise forms.ValidationError("Ce nom d'utilisateur existe deja.")
        return username

    def clean(self):
        cleaned = super().clean()
        password1 = cleaned.get("password1")
        password2 = cleaned.get("password2")
        if password1 and password2 and password1 != password2:
            self.add_error("password2", "Les deux mots de passe ne correspondent pas.")
        if password1:
            validate_password(password1)
        return cleaned

    def save(self):
        User = get_user_model()
        return User.objects.create_superuser(
            username=self.cleaned_data["username"],
            email=self.cleaned_data.get("email", ""),
            password=self.cleaned_data["password1"],
        )


class SuperuserPasswordResetForm(forms.Form):
    password1 = forms.CharField(
        label="Nouveau mot de passe",
        widget=forms.PasswordInput(attrs={"class": "form-control", "autocomplete": "new-password"}),
    )
    password2 = forms.CharField(
        label="Confirmation",
        widget=forms.PasswordInput(attrs={"class": "form-control", "autocomplete": "new-password"}),
    )

    def __init__(self, *args, target_user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.target_user = target_user

    def clean(self):
        cleaned = super().clean()
        password1 = cleaned.get("password1")
        password2 = cleaned.get("password2")
        if password1 and password2 and password1 != password2:
            self.add_error("password2", "Les deux mots de passe ne correspondent pas.")
        if password1:
            validate_password(password1, self.target_user)
        return cleaned

    def save(self):
        self.target_user.set_password(self.cleaned_data["password1"])
        self.target_user.save(update_fields=["password"])
        return self.target_user
