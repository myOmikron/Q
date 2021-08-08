import json

from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.views import LoginView, LogoutView
from django.shortcuts import render, redirect
from django.views import View
from django.views.generic import TemplateView


class Login(LoginView):
    template_name = "auth/login.html"
    success_url = "/"


class Logout(LoginRequiredMixin, LogoutView):
    template_name = ""


class DashboardView(LoginRequiredMixin, TemplateView):
    template_name = "dashboard/dashboard.html"

    def get(self, request, *args, **kwargs):
        return render(request, self.template_name)


#
# Templates for model specific classes which can call the API for creation, updating and deletion
#
class DeclarationTemplateIndex(LoginRequiredMixin, TemplateView):

    def get(self, request, model_class=None, extended_params=None, *args, **kwargs):
        template_name = f"declaration/{model_class.__name__.lower()}/index.html"
        entries = [x.to_dict() for x in model_class.objects.all()]
        return render(request, template_name, {"entries": entries, **extended_params})


class DeclarationTemplateDelete(LoginRequiredMixin, View):
    def post(self, request, sid="", model_class=None, callback_list=None, *args, **kwargs):
        try:
            entry = model_class.objects.get(id=sid)
        except model_class.DoesNotExist:
            return redirect(f"/declaration/{model_class.__name__.lower()}/")
        if callback_list:
            for x in callback_list:
                x(request, sid, model_class)
        entry.delete()
        return redirect(f"/declaration/{model_class.__name__.lower()}/")


class DeclarationTemplateCreate(LoginRequiredMixin, TemplateView):
    def get(self, request, model_class=None, extended_params=None, *args, **kwargs):
        template_name = f"declaration/{model_class.__name__.lower()}/create_or_update.html"
        return render(request, template_name, extended_params)

    def post(self, request, api_class=None, model_class=None, extended_params=None, *args, **kwargs):
        template_name = f"declaration/{model_class.__name__.lower()}/create_or_update.html"
        ret = api_class().save_post(params=request.POST)
        if ret.status_code != 200 and ret.status_code != 201:
            return render(
                request, template_name, {"error": json.loads(ret.content)["message"], **extended_params}
            )
        return redirect(f"/declaration/{model_class.__name__.lower()}/")


class DeclarationTemplateUpdate(LoginRequiredMixin, TemplateView):
    def get(self, request, sid="", model_class=None, extended_params=None, *args, **kwargs):
        template_name = f"declaration/{model_class.__name__.lower()}/create_or_update.html"
        try:
            existing = model_class.objects.get(id=sid).to_dict()
        except model_class.DoesNotExist:
            return render(
                request, "lib/error.html",
                {"error_header": f"{model_class.__name__} is not existing", **extended_params}
            )
        return render(request, template_name, {"existing": existing, **extended_params})

    def post(self, request, sid="", api_class=None, model_class=None, extended_params=None, *args, **kwargs):
        template_name = f"declaration/{model_class.__name__.lower()}/create_or_update.html"
        ret = api_class().save_put(params=request.POST, sid=sid)
        if ret.status_code != 200:
            return render(request, template_name, {"error": json.loads(ret.content)["message"]}, **extended_params)
        return redirect(f"/declaration/{model_class.__name__.lower()}/")