from __future__ import annotations

from django import forms


PUBLISH_MODES = (
    ("new", "Nouveau dataset"),
    ("version", "Nouvelle version"),
)


class DatasetCreateForm(forms.Form):
    mode = forms.ChoiceField(choices=PUBLISH_MODES)
    existing_slug = forms.CharField(required=False)
    source_code = forms.CharField()
    topic_id = forms.IntegerField(required=False)
    indicator_ids = forms.MultipleChoiceField(required=False)
    country_id = forms.IntegerField()
    start_date = forms.DateField(input_formats=["%Y-%m-%d"])
    end_date = forms.DateField(input_formats=["%Y-%m-%d"])
    title = forms.CharField(max_length=180)
    description = forms.CharField()

    def __init__(self, *args, indicator_choices=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["indicator_ids"].choices = indicator_choices or []

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("mode") == "version" and not cleaned.get("existing_slug"):
            self.add_error("existing_slug", "Sélectionnez le dataset à versionner.")
        if not cleaned.get("indicator_ids"):
            self.add_error("indicator_ids", "Sélectionnez au moins un indicateur.")
        start_date = cleaned.get("start_date")
        end_date = cleaned.get("end_date")
        if start_date and end_date and start_date > end_date:
            self.add_error("end_date", "La date de fin doit être supérieure ou égale à la date de début.")
        return cleaned
