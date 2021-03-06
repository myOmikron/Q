import secrets
import string
import json
from collections import ChainMap

from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.contenttypes.models import ContentType
from django.core.paginator import Paginator
from django.db.models import Max
from django.http import JsonResponse, HttpResponse, QueryDict
from django.views import View

from api.models import AccountModel, ACLModel, Check, Host, Observable, TimePeriod, SchedulingInterval, GenericKVP, \
    Label, Day, Period, DayTimePeriod, GlobalVariable, Contact, ContactGroup, ObservableTemplate, HostTemplate, Proxy, \
    OrderedListItem
from api.description import export


def get_variable_list(parameter):
    if len(parameter) > 1:
        return [x for x in parameter if isinstance(x, str) or isinstance(x, int)]
    else:
        if "," in parameter:
            return [x for x in parameter[0].split(",") if x]
        else:
            return parameter


class CheckMixinView(View):
    """Base View for REST API requests.

    To include required parameters in body or urlencoded, call the super().__init__() with the required parameters.

    :param required_get: List of required parameters. Defaults to [].
    :param required_post:  List of required parameters. Defaults to [].
    :param required_put: List of required parameters. Defaults to [].
    :param required_delete: List of required parameters. Defaults to [].
    """

    def __init__(self, required_get=None, required_post=None, required_put=None, required_delete=None, **kwargs):
        super().__init__(**kwargs)
        self.required_get = required_get if required_get else []
        self.required_post = required_post if required_post else []
        self.required_put = required_put if required_put else []
        self.required_delete = required_delete if required_delete else []

    def _check_auth(self, request, required_params):
        if not request.user.is_authenticated:
            return {"success": False, "message": "User is not authenticated", "status": 401}
        try:
            account = AccountModel.objects.get(internal_user__username=request.user.username)
        except AccountModel.DoesNotExist:
            return {"success": False, "message": "Username is incorrect or token expired", "status": 401}

        # Check ACLs
        for acl in account.linked_acl_group.linked_acls.all():
            if acl.name == f"API:{request.META['REQUEST_METHOD']}:{request.META['PATH_INFO']}":
                if not acl.allow:
                    return {"success": False, "message": "You are not allowed to use this", "status": 403}
                else:
                    break

        # Only POST and PUT have a body to decode
        if request.META["REQUEST_METHOD"] == "POST" or request.META["REQUEST_METHOD"] == "PUT":
            # Decode json
            try:
                # If request.body is None or an empty string, json.loads fails
                decoded = json.loads(request.body if request.body else "{}")
            except json.JSONDecodeError:
                return {"success": False, "message": "Json could not be decoded", "status": 400}
        elif request.META["REQUEST_METHOD"] == "GET" or request.META["REQUEST_METHOD"] == "DELETE":
            # Set decoded to urlencoded parameters
            decoded = request.GET

        # Check for required parameters
        for param in required_params:
            if param not in decoded:
                return {"success": False, "message": f"Parameter {param} is missing but mandatory", "status": 400}

        return {"success": True, "data": decoded}

    def get(self, request, *args, **kwargs):
        ret = self._check_auth(request, self.required_get)
        if not ret["success"]:
            return JsonResponse({"success": False, "message": ret["message"]}, status=ret["status"])
        return self.cleaned_get(ret["data"], *args, **kwargs)

    def post(self, request, *args, **kwargs):
        ret = self._check_auth(request, self.required_post)
        if not ret["success"]:
            return JsonResponse({"success": False, "message": ret["message"]}, status=ret["status"])
        return self.cleaned_post(ret["data"], *args, **kwargs)

    def put(self, request, *args, **kwargs):
        ret = self._check_auth(request, self.required_put)
        if not ret["success"]:
            return JsonResponse({"success": False, "message": ret["message"]}, status=ret["status"])
        return self.cleaned_put(ret["data"], *args, **kwargs)

    def delete(self, request, *args, **kwargs):
        ret = self._check_auth(request, self.required_delete)
        if not ret["success"]:
            return JsonResponse({"success": False, "message": ret["message"]}, status=ret["status"])
        return self.cleaned_delete(ret["data"], *args, **kwargs)

    def cleaned_get(self, params, *args, **kwargs):
        return NotImplemented

    def cleaned_post(self, params, *args, **kwargs):
        return NotImplemented

    def cleaned_put(self, params, *args, **kwargs):
        return NotImplemented

    def cleaned_delete(self, params, *args, **kwargs):
        return NotImplemented


class CheckOptionalMixinView(CheckMixinView):
    def __init__(
            self, api_class=None, **kwargs
    ):
        super(CheckOptionalMixinView, self).__init__(
            **kwargs
        )
        self.api_class = api_class

    def cleaned_get(self, params: QueryDict, *args, **kwargs):
        if "sid" in kwargs:
            if not isinstance(kwargs["sid"], int) and not isinstance(kwargs["sid"], str):
                return JsonResponse({"success": False, "message": "ID has to be str or int"})
            try:
                data = {}
                if "values" in params:
                    values = get_variable_list(params.getlist("values"))
                    if any(x not in self.api_class.allowed_values.keys() for x in values):
                        return JsonResponse({"success": False, "message": "Bad values parameter"}, status=400)
                    values = dict(ChainMap(*[{x[0]: x[1]} for x in self.api_class.allowed_values.items() if x[0] in values]))
                    item = self.api_class.objects.get(id=kwargs["sid"])
                    tmp = item.to_dict(values=values.keys())
                    data["id"] = item.id
                    for value in values:
                        data[value] = tmp.__getitem__(v)
                else:
                    data = self.api_class.objects.get(id=kwargs["sid"]).to_dict()
                return JsonResponse({"success": True, "data": data})
            except self.api_class.DoesNotExist:
                return JsonResponse(
                    {"success": False, "message": f"{self.api_class.__name__} with id {kwargs['sid']} does not exist"}
                )
        else:
            if "p" not in params:
                return JsonResponse({"success": False, "message": "Parameter p is required but missing"}, status=400)
            current_page = params["p"]
            values = None
            if "values" in params:
                values = get_variable_list(params.getlist("values"))
                if any([x not in self.api_class.allowed_values for x in values]):
                    return JsonResponse({"success": False, "message": "Bad values parameter"}, status=400)
                values = dict(
                    ChainMap(*[
                        {x[0]: x[1]}
                        for x in self.api_class.allowed_values.items() if x[0] in values
                    ])
                )
            if "filter" in params:
                if isinstance(params["filter"], list):
                    if "values" in params:
                        items = self.api_class.objects.filter(id__in=params["filter"]).only(*values.values())
                    else:
                        items = self.api_class.objects.filter(id__in=params["filter"])
                else:
                    if "values" in params:
                        items = self.api_class.objects.get(id=str(params["filter"])).only(*values.values())
                    else:
                        items = self.api_class.objects.get(id=str(params["filter"]))
            else:
                if "query" in params and params["query"]:
                    if "name" not in self.api_class.allowed_values.keys():
                        return JsonResponse({"success": False, "message": "Object does not support query"}, status=400)
                    query = str(params.get("query"))
                    if "values" in params:
                        items = self.api_class.objects.filter(name__icontains=query).only(*values.values())
                    else:
                        items = self.api_class.objects.filter(name__icontains=query)
                else:
                    if "values" in params:
                        items = self.api_class.objects.all().only(*values.values())
                    else:
                        items = self.api_class.objects.all()

            paginator = Paginator(items, 50)

            page = paginator.get_page(current_page)
            page_items = page.object_list

            if "values" in params:
                data = [x.to_dict(values=values.keys()) for x in page_items]
            else:
                data = [x.to_dict() for x in page_items]

            if "filter" in params and isinstance(params["filter"], list):
                data = data[0]

        return JsonResponse({
            "success": True,
            "message": "Request was successful",
            "data": data,
            "pagination": {
                "page_count": paginator.num_pages,
                "object_count": paginator.count,
                "objects_per_page": paginator.per_page,
                "current_page": page.number
            }
        })

    def cleaned_post(self, params, *args, **kwargs):
        if "sid" in kwargs:
            return HttpResponse(status=405)
        return self.save_post(params, *args, **kwargs)

    def cleaned_put(self, params, *args, **kwargs):
        if "sid" not in kwargs:
            return HttpResponse(status=405)
        return self.save_put(params, *args, **kwargs)

    def cleaned_delete(self, params, *args, **kwargs):
        if "sid" not in kwargs:
            return HttpResponse(status=405)
        try:
            obj = self.api_class.objects.get(id=kwargs["sid"])
        except self.api_class.DoesNotExist:
            return JsonResponse(
                {"success": False, "message": f"{self.api_class.__name__} with id {kwargs['sid']} does not exist"},
                status=404
            )
        obj.delete()
        return JsonResponse({"success": True, "message": f"{self.api_class.__name__} was deleted"})

    def save_post(self, params, *args, **kwargs):
        return NotImplemented

    def save_put(self, params, *args, **kwargs):
        return NotImplemented


class AuthenticateView(View):
    def post(self, request, *args, **kwargs):
        try:
            decoded = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse(
                {"success": False, "message": "JSON could not be decoded"},
                status=400
            )
        if "username" not in decoded:
            return JsonResponse(
                {"success": False, "message": "Parameter username is required, but missing"},
                status=400
            )
        if "password" not in decoded:
            return JsonResponse(
                {"success": False, "message": "Parameter password is required, but missing"},
                status=400
            )
        user = authenticate(username=decoded["username"], password=decoded["password"])
        if not user:
            return JsonResponse(
                {"success": False, "message": "Username or password is incorrect"},
                status=401
            )
        account: AccountModel = user.accountmodel_set.first()
        if not account:
            return JsonResponse(
                {"success": False, "message": "Error retrieving account"},
                status=500
            )

        # Check ACLs
        try:
            acl = account.linked_acl_group.linked_acls.get(name="API:POST:/api/v1/authenticate")
        except ACLModel.DoesNotExist:
            return JsonResponse({"success": False, "message": "Error retrieving ACL"}, status=500)
        if not acl.allow:
            return JsonResponse({"success": False, "message": "You are not allowed to use this"}, status=403)

        login(request, user)
        return JsonResponse({"success": True, "message": "Logged in successfully"})


class TestView(View):
    def get(self, request, *args, **kwargs):
        return JsonResponse({"success": request.user.is_authenticated})


class Logout(LoginRequiredMixin, View):
    def get(self, request, *args, **kwargs):
        logout(request)
        return JsonResponse({"success": True})

    def post(self, request, *args, **kwargs):
        logout(request)
        return JsonResponse({"success": True})


class CheckView(CheckOptionalMixinView):
    def __init__(self, **kwargs):
        super().__init__(
            api_class=Check,
            required_post=["name"],
            **kwargs
        )

    def optional(self, check, params):
        if "comment" in params:
            check.comment = params["comment"]
        if "cmd" in params:
            check.cmd = params["cmd"]

    def save_post(self, params, *args, **kwargs):
        check, created = Check.objects.get_or_create(name=params["name"])
        if not created:
            return JsonResponse({"success": False, "message": "Check already exists with that name"}, status=409)

        self.optional(check, params)

        check.save()
        return JsonResponse({"success": True, "message": "Object was created", "data": check.id})

    def save_put(self, params, *args, **kwargs):
        try:
            check = Check.objects.get(id=kwargs["sid"])
        except Check.DoesNotExist:
            return JsonResponse(
                {"success": False, "message": f"Check with id {kwargs['sid']} does not exist"}, status=404
            )
        if "name" in params:
            if not params["name"]:
                return JsonResponse({"success": False, "message": "Parameter name cannot be empty"}, status=400)
            if check.name != params["name"] and Check.objects.filter(name=params["name"]).count() > 0:
                return JsonResponse({"success": False, "message": "Parameter name must be unique"}, status=409)
            check.name = params["name"]

        self.optional(check, params)
        check.save()
        return JsonResponse({"success": True, "message": "Changes were successful"})


class ObservableView(CheckOptionalMixinView):
    def __init__(self, **kwargs):
        super(ObservableView, self).__init__(
            api_class=Observable,
            required_post=["name", "linked_host", "linked_proxy"],
            **kwargs
        )

    def optional(self, observable, params, overwrite=False):
        if "comment" in params:
            observable.comment = params["comment"]
        if "linked_check" in params:
            if params["linked_check"]:
                try:
                    linked_check = Check.objects.get(id=params["linked_check"])
                    observable.linked_check = linked_check
                except Check.DoesNotExist:
                    return JsonResponse(
                        {"success": False, "message": f"Check with id {params['linked_check']} does not exist"},
                        status=404
                    )
            else:
                observable.linked_check = None
        if "observable_templates" in params:
            if params["observable_templates"]:
                if overwrite:
                    observable.observable_templates.clear()
                if isinstance(params["observable_templates"], list):
                    for mt_id in params["observable_templates"]:
                        mt = ObservableTemplate.objects.get(id=mt_id)
                        max_index = 1 if observable.observable_templates.count() == 0 else \
                            observable.observable_templates.all().aggregate(Max("index"))["index__max"] + 1
                        ordered_item = OrderedListItem.objects.create(
                            index=max_index,
                            object_id=mt.id,
                            content_type=ContentType.objects.get_for_model(ObservableTemplate)
                        )
                        observable.observable_templates.add(ordered_item)
                else:
                    try:
                        mt = ObservableTemplate.objects.get(id=params["observable_templates"])
                        max_index = 1 if observable.observable_templates.count() == 0 else \
                            observable.observable_templates.all().aggregate(Max("index"))["index__max"] + 1
                        ordered_item = OrderedListItem.objects.create(
                            index=max_index,
                            object_id=mt.id,
                            content_type=ContentType.objects.get_for_model(ObservableTemplate)
                        )
                        observable.observable_templates.add(ordered_item)
                    except ObservableTemplate.DoesNotExist:
                        return JsonResponse(
                            {"success": False,
                             "message": f"Observable Template with the id {params['observable_templates']} does not exist"},
                            status=404
                        )
            else:
                observable.observable_templates.clear()
        if "linked_contacts" in params:
            if params["linked_contacts"]:
                if overwrite:
                    observable.linked_contacts.clear()
                if isinstance(params["linked_contacts"], list):
                    for contact_id in params['linked_contacts']:
                        try:
                            contact = Contact.objects.get(id=contact_id)
                            observable.linked_contacts.add(contact)
                        except Contact.DoesNotExist:
                            return JsonResponse(
                                {
                                    "success": False,
                                    "message": f"Contact with id {params['linked_contacts']} does not exist"
                                }, status=404
                            )
                else:
                    try:
                        contact = Contact.objects.get(id=params["linked_contacts"])
                        observable.linked_contacts.add(contact)
                    except Contact.DoesNotExist:
                        return JsonResponse(
                            {
                                "success": False,
                                "message": f"Contact with id {params['linked_contacts']} does not exist"
                            }, status=404
                        )
            else:
                observable.linked_contacts.clear()
        if "linked_contact_groups" in params:
            if params["linked_contact_groups"]:
                if overwrite:
                    observable.linked_contact_groups.clear()
                if isinstance(params["linked_contact_groups"], list):
                    for contact_group_id in params['linked_contact_groups']:
                        try:
                            contact_group = ContactGroup.objects.get(id=contact_group_id)
                            observable.linked_contact_groups.add(contact_group)
                        except ContactGroup.DoesNotExist:
                            return JsonResponse(
                                {
                                    "success": False,
                                    "message": f"ContactGroup with id {params['linked_contact_groups']} does not exist"
                                }, status=404
                            )
                else:
                    try:
                        contact_group = ContactGroup.objects.get(id=params["linked_contact_groups"])
                        observable.linked_contact_groups.add(contact_group)
                    except ContactGroup.DoesNotExist:
                        return JsonResponse(
                            {
                                "success": False,
                                "message": f"ContactGroup with id {params['linked_contact_group']} does not exist"
                            }, status=404
                        )
            else:
                observable.linked_contact_groups.clear()
        if "scheduling_interval" in params:
            if params["scheduling_interval"]:
                scheduling_interval, _ = SchedulingInterval.objects.get_or_create(
                    interval=params["scheduling_interval"])
                observable.scheduling_interval = scheduling_interval
            else:
                observable.scheduling_interval = None
        if "scheduling_period" in params:
            if params["scheduling_period"]:
                try:
                    scheduling_period = TimePeriod.objects.get(id=params["scheduling_period"])
                    observable.scheduling_period = scheduling_period
                except TimePeriod.DoesNotExist:
                    return JsonResponse(
                        {"success": False,
                         "message": f"TimePeriod with id {params['scheduling_period']} does not exist"},
                        status=404
                    )
            else:
                observable.scheduling_period = None
        if "disabled" in params:
            disabled = params["disabled"]
            observable.disabled = disabled
        if "notification_period" in params:
            if params["notification_period"]:
                try:
                    notification_period = TimePeriod.objects.get(id=params["notification_period"])
                    observable.notification_period = notification_period
                except TimePeriod.DoesNotExist:
                    return JsonResponse(
                        {"success": False,
                         "message": f"TimePeriod with id {params['notification_period']} does not exist"},
                        status=404
                    )
            else:
                observable.notification_period = None
        if "variables" in params:
            if not isinstance(params["variables"], dict) and not isinstance(params["variables"], str):
                return JsonResponse({"success": False, "message": "Parameter variables has to be a dict"}, status=400)
            if params["variables"]:
                if overwrite:
                    observable.variable.clear()
                for key, value in params["variables"].items():
                    key_label, _ = Label.objects.get_or_create(label=key)
                    value_label, _ = Label.objects.get_or_create(label=value)
                    GenericKVP.objects.get_or_create(
                        key=key_label, value=value_label, object_id=observable.id,
                        content_type=ContentType.objects.get_for_model(Observable)
                    )
            else:
                observable.variables.clear()

    def save_post(self, params, *args, **kwargs):
        # Required params
        try:
            linked_host = Host.objects.get(id=params["linked_host"])
        except Host.DoesNotExist:
            return JsonResponse(
                {"success": False, "message": f"Host with id {params['linked_host']} does not exist"},
                status=404
            )
        try:
            linked_proxy = Proxy.objects.get(id=params["linked_proxy"])
        except Proxy.DoesNotExist:
            return JsonResponse(
                {"success": False, "message": f"Proxy with id {params['linked_proxy']} does not exist"},
                status=404
            )

        # Create check
        if Observable.objects.filter(name=params["name"], linked_host=linked_host).exists():
            return JsonResponse(
                {"success": False, "message": "Metric with this name already exists"}, status=409
            )
        metric = Observable.objects.create(name=params["name"], linked_host=linked_host, linked_proxy=linked_proxy)
        # Optional params
        ret = self.optional(metric, params)
        if isinstance(ret, JsonResponse):
            metric.delete()
            return ret

        # Save and return
        metric.save()
        return JsonResponse({"success": True, "message": "Created metric successfully", "data": metric.id})

    def save_put(self, params, *args, **kwargs):
        try:
            metric = Observable.objects.get(id=kwargs["sid"])
        except Observable.DoesNotExist:
            return JsonResponse(
                {"success": False, "message": f"Metric with id {kwargs['sid']} not exist"}
            )
        if "linked_host" in params:
            try:
                linked_host = Host.objects.get(id=params["linked_host"])
                metric.linked_host = linked_host
            except Host.DoesNotExist:
                return JsonResponse(
                    {"success": False, "message": f"Host with id {params['linked_host']} does not exist"},
                    status=404
                )
        if "linked_proxy" in params:
            try:
                proxy = Proxy.objects.get(id=params["linked_proxy"])
                metric.linked_proxy = proxy
            except Proxy.DoesNotExist:
                return JsonResponse(
                    {"success": False, "message": f"Proxy with id {params['linked_proxy']} does not exist"}, status=404
                )
        if "name" in params:
            metric.name = params["name"]

        ret = self.optional(metric, params, overwrite=True)
        if isinstance(ret, JsonResponse):
            return ret

        metric.save()
        return JsonResponse({"success": True, "message": "Changes were successful"})


class ObservableTemplateView(CheckOptionalMixinView):
    def __init__(self):
        super(ObservableTemplateView, self).__init__(
            api_class=ObservableTemplate,
            required_post=["name"],
        )

    def optional(self, observable_template, params, overwrite=False):
        if "comment" in params:
            observable_template.comment = params["comment"]
        if "linked_check" in params:
            if params["linked_check"]:
                try:
                    linked_check = Check.objects.get(id=params["linked_check"])
                    observable_template.linked_check = linked_check
                except Check.DoesNotExist:
                    return JsonResponse(
                        {"success": False, "message": f"Check with id {params['linked_check']} does not exist"},
                        status=404
                    )
            else:
                observable_template.linked_check = None
        if "observable_templates" in params:
            if overwrite:
                observable_template.observable_templates.clear()
            if params["observable_templates"]:
                if isinstance(params["observable_templates"], list):
                    for mt_id in params["observable_templates"]:
                        mt = ObservableTemplate.objects.get(id=mt_id)
                        max_index = 1 if observable_template.observable_templates.count() == 0 else \
                            observable_template.observable_templates.all().aggregate(Max("index"))["index__max"] + 1
                        ordered_item = OrderedListItem.objects.create(
                            index=max_index,
                            object_id=mt.id,
                            content_type=ContentType.objects.get_for_model(ObservableTemplate)
                        )
                        observable_template.observable_templates.add(ordered_item)
                else:
                    mt = ObservableTemplate.objects.get(id=params["observable_templates"])
                    max_index = 1 if observable_template.observable_templates.count() == 0 else \
                        observable_template.observable_templates.all().aggregate(Max("index"))["index__max"] + 1
                    ordered_item = OrderedListItem.objects.create(
                        index=max_index,
                        object_id=mt.id,
                        content_type=ContentType.objects.get_for_model(ObservableTemplate)
                    )
                    observable_template.observable_templates.add(ordered_item)
            else:
                observable_template.observable_templates.clear()
        if "linked_contacts" in params:
            if params["linked_contacts"]:
                if overwrite:
                    observable_template.linked_contacts.clear()
                if isinstance(params["linked_contacts"], list):
                    for contact_id in params['linked_contacts']:
                        try:
                            contact = Contact.objects.get(id=contact_id)
                            observable_template.linked_contacts.add(contact)
                        except Contact.DoesNotExist:
                            return JsonResponse(
                                {
                                    "success": False,
                                    "message": f"Contact with id {params['linked_contacts']} does not exist"
                                }, status=404
                            )
                else:
                    try:
                        contact = Contact.objects.get(id=params["linked_contacts"])
                        observable_template.linked_contacts.add(contact)
                    except Contact.DoesNotExist:
                        return JsonResponse(
                            {
                                "success": False,
                                "message": f"Contact with id {params['linked_contacts']} does not exist"
                            }, status=404
                        )
            else:
                observable_template.linked_contacts.clear()
        if "linked_contact_groups" in params:
            if params["linked_contact_groups"]:
                if overwrite:
                    observable_template.linked_contact_groups.clear()
                if isinstance(params["linked_contact_groups"], list):
                    for contact_group_id in params['linked_contact_groups']:
                        try:
                            contact_group = ContactGroup.objects.get(id=contact_group_id)
                            observable_template.linked_contact_groups.add(contact_group)
                        except ContactGroup.DoesNotExist:
                            return JsonResponse(
                                {
                                    "success": False,
                                    "message": f"ContactGroup with id {params['linked_contact_groups']} does not exist"
                                }, status=404
                            )
                else:
                    try:
                        contact_group = ContactGroup.objects.get(id=params["linked_contact_groups"])
                        observable_template.linked_contact_groups.add(contact_group)
                    except ContactGroup.DoesNotExist:
                        return JsonResponse(
                            {
                                "success": False,
                                "message": f"ContactGroup with id {params['linked_contact_group']} does not exist"
                            }, status=404
                        )
            else:
                observable_template.linked_contact_groups.clear()
        if "scheduling_interval" in params:
            if params["scheduling_interval"]:
                scheduling_interval, _ = SchedulingInterval.objects.get_or_create(
                    interval=params["scheduling_interval"])
                observable_template.scheduling_interval = scheduling_interval
            else:
                observable_template.scheduling_interval = None
        if "scheduling_period" in params:
            if params["scheduling_period"]:
                try:
                    scheduling_period = TimePeriod.objects.get(id=params["scheduling_period"])
                    observable_template.scheduling_period = scheduling_period
                except TimePeriod.DoesNotExist:
                    return JsonResponse(
                        {"success": False,
                         "message": f"TimePeriod with id {params['scheduling_period']} does not exist"},
                        status=404
                    )
            else:
                observable_template.scheduling_period = None
        if "notification_period" in params:
            if params["notification_period"]:
                try:
                    notification_period = TimePeriod.objects.get(id=params["notification_period"])
                    observable_template.notification_period = notification_period
                except TimePeriod.DoesNotExist:
                    return JsonResponse(
                        {"success": False,
                         "message": f"TimePeriod with id {params['notification_period']} does not exist"},
                        status=404
                    )
            else:
                observable_template.notification_period = None
        if "variables" in params:
            if not isinstance(params["variables"], dict) and not isinstance(params["variables"], str):
                return JsonResponse({"success": False, "message": "Parameter variables has to be a dict"}, status=400)
            if not params["variables"]:
                observable_template.variables.clear()
            else:
                if overwrite:
                    observable_template.variables.clear()
                for key, value in params["variables"].items():
                    key_label, _ = Label.objects.get_or_create(label=key)
                    value_label, _ = Label.objects.get_or_create(label=value)
                    GenericKVP.objects.get_or_create(
                        key=key_label, value=value_label, object_id=observable_template.id,
                        content_type=ContentType.objects.get_for_model(ObservableTemplate)
                    )

    def save_post(self, params, *args, **kwargs):
        # Create check
        if ObservableTemplate.objects.filter(name=params["name"]).exists():
            return JsonResponse(
                {"success": False, "message": "MetricTemplate with this name already exists"},
                status=409
            )
        observable_template = ObservableTemplate.objects.create(name=params["name"])
        # Optional params
        ret = self.optional(observable_template, params)
        if isinstance(ret, JsonResponse):
            observable_template.delete()
            return ret

        # Save and return
        observable_template.save()
        return JsonResponse(
            {"success": True, "message": "Created Observable Template successfully", "data": observable_template.id}
        )

    def save_put(self, params, *args, **kwargs):
        try:
            observable_template = ObservableTemplate.objects.get(id=kwargs["sid"])
        except ObservableTemplate.DoesNotExist:
            return JsonResponse(
                {"success": False, "message": f"Observable Template with id {kwargs['sid']} not exist"}
            )
        if "name" in params:
            observable_template.name = params["name"]

        ret = self.optional(observable_template, params, overwrite=True)
        if isinstance(ret, JsonResponse):
            return ret

        observable_template.save()
        return JsonResponse({"success": True, "message": "Changes were successful"})


class HostView(CheckOptionalMixinView):
    def __init__(self):
        super(HostView, self).__init__(
            api_class=Host,
            required_post=["name", "linked_proxy"]
        )

    def optional(self, host, params, overwrite=False):
        if "comment" in params:
            host.comment = params["comment"]
        if "address" in params:
            host.address = params["address"]
        if "linked_check" in params:
            if params["linked_check"]:
                try:
                    check = Check.objects.get(id=params["linked_check"])
                    host.linked_check = check
                except Check.DoesNotExist:
                    return JsonResponse(
                        {"success": False, "message": f"Check with id {params['linked_check']} does not exist"},
                        status=404
                    )
            else:
                host.linked_check = None
        if "disabled" in params:
            host.disabled = params["disabled"]
        if "host_templates" in params:
            if params["host_templates"]:
                if overwrite:
                    host.host_templates.clear()
                if isinstance(params["host_templates"], list):
                    for ht_id in params["host_templates"]:
                        try:
                            ht = HostTemplate.objects.get(id=ht_id)
                            max_index = 1 if host.host_templates.count() == 0 else \
                                host.host_templates.all().aggregate(Max("index"))["index__max"] + 1
                            ordered_item = OrderedListItem.objects.create(
                                index=max_index,
                                object_id=ht.id,
                                content_type=ContentType.objects.get_for_model(HostTemplate)
                            )
                            host.host_templates.add(ordered_item)
                        except HostTemplate.DoesNotExist:
                            return JsonResponse(
                                {"success": False, "message": f"HostTemplate with id {ht_id} does not exist"},
                                status=404
                            )
                else:
                    try:
                        ht = HostTemplate.objects.get(id=params["host_templates"])
                        max_index = 1 if host.host_templates.count() == 0 else \
                            host.host_templates.all().aggregate(Max("index"))["index__max"] + 1
                        ordered_item = OrderedListItem.objects.create(
                            index=max_index,
                            object_id=ht.id,
                            content_type=ContentType.objects.get_for_model(HostTemplate)
                        )
                        host.host_templates.add(ordered_item)
                    except HostTemplate.DoesNotExist:
                        return JsonResponse(
                            {
                                "success": False,
                                "message": f"HostTemplate with id {params['host_templates']} does not exist"
                            }, status=404
                        )
            else:
                host.host_templates.clear()
        if "linked_contacts" in params:
            if params["linked_contacts"]:
                if overwrite:
                    host.linked_contacts.clear()
                if isinstance(params["linked_contacts"], list):
                    for contact_id in params['linked_contacts']:
                        try:
                            contact = Contact.objects.get(id=contact_id)
                            host.linked_contacts.add(contact)
                        except Contact.DoesNotExist:
                            return JsonResponse(
                                {
                                    "success": False,
                                    "message": f"Contact with id {params['linked_contacts']} does not exist"
                                }, status=404
                            )
                else:
                    try:
                        contact = Contact.objects.get(id=params["linked_contacts"])
                        host.linked_contacts.add(contact)
                    except Contact.DoesNotExist:
                        return JsonResponse(
                            {
                                "success": False,
                                "message": f"Contact with id {params['linked_contacts']} does not exist"
                            }, status=404
                        )
            else:
                host.linked_contacts.clear()
        if "linked_contact_groups" in params:
            if params["linked_contact_groups"]:
                if overwrite:
                    host.linked_contact_groups.clear()
                if isinstance(params["linked_contact_groups"], list):
                    for contact_group_id in params['linked_contact_groups']:
                        try:
                            contact_group = ContactGroup.objects.get(id=contact_group_id)
                            host.linked_contact_groups.add(contact_group)
                        except ContactGroup.DoesNotExist:
                            return JsonResponse(
                                {
                                    "success": False,
                                    "message": f"ContactGroup with id {params['linked_contact_groups']} does not exist"
                                }, status=404
                            )
                else:
                    try:
                        contact_group = ContactGroup.objects.get(id=params["linked_contact_groups"])
                        host.linked_contact_groups.add(contact_group)
                    except ContactGroup.DoesNotExist:
                        return JsonResponse(
                            {
                                "success": False,
                                "message": f"ContactGroup with id {params['linked_contact_group']} does not exist"
                            }, status=404
                        )
            else:
                host.linked_contact_groups.clear()
        if "scheduling_interval" in params:
            if params["scheduling_interval"]:
                scheduling_interval, _ = SchedulingInterval.objects.get_or_create(
                    interval=params["scheduling_interval"])
                host.scheduling_interval = scheduling_interval
            else:
                host.scheduling_interval = None
        if "scheduling_period" in params:
            if params["scheduling_period"]:
                try:
                    scheduling_period = TimePeriod.objects.get(id=params["scheduling_period"])
                    host.scheduling_period = scheduling_period
                except TimePeriod.DoesNotExist:
                    return JsonResponse(
                        {"success": False,
                         "message": f"TimePeriod with id {params['scheduling_period']} does not exist"},
                        status=404
                    )
            else:
                host.scheduling_period = None
        if "notification_period" in params:
            if params["notification_period"]:
                try:
                    notification_period = TimePeriod.objects.get(id=params["notification_period"])
                    host.notification_period = notification_period
                except TimePeriod.DoesNotExist:
                    return JsonResponse(
                        {"success": False,
                         "message": f"TimePeriod with id {params['notification_period']} does not exist"},
                        status=404
                    )
            else:
                host.notification_period = None
        if "variables" in params:
            if overwrite:
                host.variables.clear()
            if not isinstance(params["variables"], dict) and not isinstance(params["variables"], str):
                return JsonResponse({"success": False, "message": "Parameter variables has to be a dict"}, status=400)
            if params["variables"]:
                for key, value in params["variables"].items():
                    key_label, _ = Label.objects.get_or_create(label=key)
                    value_label, _ = Label.objects.get_or_create(label=value)
                    GenericKVP.objects.get_or_create(
                        key=key_label, value=value_label, object_id=host.id,
                        content_type=ContentType.objects.get_for_model(Host)
                    )
            else:
                host.variables.clear()

    def save_post(self, params, *args, **kwargs):
        if Host.objects.filter(name=params["name"]).exists():
            return JsonResponse(
                {"success": False, "message": f"Host with name {params['name']} already exists"}, status=409
            )
        try:
            linked_proxy = Proxy.objects.get(id=params["linked_proxy"])
        except Proxy.DoesNotExist:
            return JsonResponse(
                {"success": False, "message": f"Proxy with id {params['linked_proxy']} does not exist"},
                status=404
            )
        host = Host.objects.create(name=params["name"], linked_proxy=linked_proxy)

        ret = self.optional(host, params)
        if isinstance(ret, JsonResponse):
            host.delete()
            return ret

        host.save()
        return JsonResponse(
            {"success": True, "message": "Host was created successful", "data": host.id}
        )

    def save_put(self, params, *args, **kwargs):
        try:
            host = Host.objects.get(id=kwargs["sid"])
        except Host.DoesNotExist:
            return JsonResponse(
                {"success": False, "message": f"Host with id {kwargs['sid']} does not exist"}, status=404
            )

        if "name" in params:
            if Host.objects.filter(name=params["name"]).exists():
                if params["name"] != host.name:
                    return JsonResponse(
                        {"success": False, "message": f"Host with name {params['name']} already exists"}, status=409
                    )
            host.name = params["name"]
        if "linked_proxy" in params:
            try:
                proxy = Proxy.objects.get(id=params["linked_proxy"])
                host.linked_proxy = proxy
            except Proxy.DoesNotExist:
                return JsonResponse(
                    {"success": False, "message": f"Proxy with id {params['linked_proxy']} does not exist"}, status=404
                )

        ret = self.optional(host, params, overwrite=True)
        if isinstance(ret, JsonResponse):
            return ret

        host.save()
        return JsonResponse({"success": True, "message": "Changes were successful"})


class HostTemplateView(CheckOptionalMixinView):
    def __init__(self):
        super(HostTemplateView, self).__init__(
            api_class=HostTemplate,
            required_post=["name"]
        )

    def optional(self, host_template, params, overwrite=False):
        if "comment" in params:
            host_template.comment = params["comment"]
        if "address" in params:
            host_template.address = params["address"]
        if "linked_check" in params:
            if params["linked_check"]:
                try:
                    check = Check.objects.get(id=params["linked_check"])
                    host_template.linked_check = check
                except Check.DoesNotExist:
                    return JsonResponse(
                        {"success": False, "message": f"Check with id {params['linked_check']} does not exist"},
                        status=404
                    )
            else:
                host_template.linked_check = None
        if "host_templates" in params:
            if params["host_templates"]:
                if overwrite:
                    host_template.host_templates.clear()
                if isinstance(params["host_templates"], list):
                    for ht_id in params["host_templates"]:
                        try:
                            ht = HostTemplate.objects.get(id=ht_id)
                            max_index = 1 if host_template.host_templates.count() == 0 else \
                                host_template.host_templates.all().aggregate(Max("index"))["index__max"] + 1
                            ordered_item = OrderedListItem.objects.create(
                                index=max_index,
                                object_id=ht.id,
                                content_type=ContentType.objects.get_for_model(HostTemplate)
                            )
                            host_template.host_templates.add(ordered_item)
                        except HostTemplate.DoesNotExist:
                            return JsonResponse(
                                {"success": False, "message": f"HostTemplate with id {ht_id} does not exist"},
                                status=404
                            )
                else:
                    try:
                        ht = HostTemplate.objects.get(id=params["host_templates"])
                        max_index = 1 if host_template.host_templates.count() == 0 else \
                            host_template.host_templates.all().aggregate(Max("index"))["index__max"] + 1
                        ordered_item = OrderedListItem.objects.create(
                            index=max_index,
                            object_id=ht.id,
                            content_type=ContentType.objects.get_for_model(HostTemplate)
                        )
                        host_template.host_templates.add(ordered_item)
                    except HostTemplate.DoesNotExist:
                        return JsonResponse(
                            {
                                "success": False,
                                "message": f"HostTemplate with id {params['host_templates']} does not exist"
                            }, status=404
                        )
            else:
                host_template.host_templates.clear()
        if "linked_contacts" in params:
            if params["linked_contacts"]:
                if overwrite:
                    host_template.linked_contacts.clear()
                if isinstance(params["linked_contacts"], list):
                    for contact_id in params['linked_contacts']:
                        try:
                            contact = Contact.objects.get(id=contact_id)
                            host_template.linked_contacts.add(contact)
                        except Contact.DoesNotExist:
                            return JsonResponse(
                                {
                                    "success": False,
                                    "message": f"Contact with id {params['linked_contacts']} does not exist"
                                }, status=404
                            )
                else:
                    try:
                        contact = Contact.objects.get(id=params["linked_contacts"])
                        host_template.linked_contacts.add(contact)
                    except Contact.DoesNotExist:
                        return JsonResponse(
                            {
                                "success": False,
                                "message": f"Contact with id {params['linked_contacts']} does not exist"
                            }, status=404
                        )
            else:
                host_template.linked_contacts.clear()
        if "linked_contact_groups" in params:
            if params["linked_contact_groups"]:
                if overwrite:
                    host_template.linked_contact_groups.clear()
                if isinstance(params["linked_contact_groups"], list):
                    for contact_group_id in params['linked_contact_groups']:
                        try:
                            contact_group = ContactGroup.objects.get(id=contact_group_id)
                            host_template.linked_contact_groups.add(contact_group)
                        except ContactGroup.DoesNotExist:
                            return JsonResponse(
                                {
                                    "success": False,
                                    "message": f"ContactGroup with id {params['linked_contact_groups']} does not exist"
                                }, status=404
                            )
                else:
                    try:
                        contact_group = ContactGroup.objects.get(id=params["linked_contact_groups"])
                        host_template.linked_contact_groups.add(contact_group)
                    except ContactGroup.DoesNotExist:
                        return JsonResponse(
                            {
                                "success": False,
                                "message": f"ContactGroup with id {params['linked_contact_group']} does not exist"
                            }, status=404
                        )
            else:
                host_template.linked_contact_groups.clear()
        if "scheduling_interval" in params:
            if params["scheduling_interval"]:
                scheduling_interval, _ = SchedulingInterval.objects.get_or_create(
                    interval=params["scheduling_interval"])
                host_template.scheduling_interval = scheduling_interval
            else:
                host_template.scheduling_interval = None
        if "scheduling_period" in params:
            if params["scheduling_period"]:
                try:
                    scheduling_period = TimePeriod.objects.get(id=params["scheduling_period"])
                    host_template.scheduling_period = scheduling_period
                except TimePeriod.DoesNotExist:
                    return JsonResponse(
                        {"success": False,
                         "message": f"TimePeriod with id {params['scheduling_period']} does not exist"},
                        status=404
                    )
            else:
                host_template.scheduling_period = None
        if "notification_period" in params:
            if params["notification_period"]:
                try:
                    notification_period = TimePeriod.objects.get(id=params["notification_period"])
                    host_template.notification_period = notification_period
                except TimePeriod.DoesNotExist:
                    return JsonResponse(
                        {"success": False,
                         "message": f"TimePeriod with id {params['notification_period']} does not exist"},
                        status=404
                    )
            else:
                host_template.notification_period = None
        if "variables" in params:
            if overwrite:
                host_template.variables.clear()
            if not isinstance(params["variables"], dict) and not isinstance(params["variables"], str):
                return JsonResponse({"success": False, "message": "Parameter variables has to be a dict"}, status=400)
            if params["variables"]:
                for key, value in params["variables"].items():
                    key_label, _ = Label.objects.get_or_create(label=key)
                    value_label, _ = Label.objects.get_or_create(label=value)
                    GenericKVP.objects.get_or_create(
                        key=key_label, value=value_label, object_id=host_template.id,
                        content_type=ContentType.objects.get_for_model(HostTemplate)
                    )
            else:
                host_template.variables.clear()

    def save_post(self, params, *args, **kwargs):
        if HostTemplate.objects.filter(name=params["name"]).exists():
            return JsonResponse(
                {"success": False, "message": f"HostTemplate with name {params['name']} already exists"}, status=409
            )
        host_template = HostTemplate.objects.create(name=params["name"])

        ret = self.optional(host_template, params)
        if isinstance(ret, JsonResponse):
            host_template.delete()
            return ret

        host_template.save()
        return JsonResponse(
            {"success": True, "message": "HostTemplate was created successful", "data": host_template.id}
        )

    def save_put(self, params, *args, **kwargs):
        try:
            host_template = HostTemplate.objects.get(id=kwargs["sid"])
        except HostTemplate.DoesNotExist:
            return JsonResponse(
                {"success": False, "message": f"HostTemplate with id {kwargs['sid']} does not exist"}, status=404
            )

        if "name" in params:
            if HostTemplate.objects.filter(name=params["name"]).exists():
                if params["name"] != host_template.name:
                    return JsonResponse(
                        {"success": False, "message": f"HostTemplate with name {params['name']} already exists"},
                        status=409
                    )
            host_template.name = params["name"]

        ret = self.optional(host_template, params, overwrite=True)
        if isinstance(ret, JsonResponse):
            return ret

        host_template.save()
        return JsonResponse({"success": True, "message": "Changes were successful"})


class TimePeriodView(CheckOptionalMixinView):
    def __init__(self):
        super(TimePeriodView, self).__init__(
            api_class=TimePeriod,
            required_post=["name", "time_periods"]
        )

    def check_param_time_periods(self, time_periods):
        if not isinstance(time_periods, dict):
            return False
        days = [x.name for x in Day.objects.all()]
        for key in time_periods:
            if key not in days:
                return False
        for day in days:
            if day not in time_periods:
                return False
        for key in time_periods:
            if not isinstance(time_periods[key], list):
                return False
            for item in time_periods[key]:
                if not isinstance(item, dict):
                    return False
                if "start_time" not in item.keys() or "stop_time" not in item.keys():
                    return False
                if not isinstance(item["start_time"], str) \
                        and not isinstance(item["start_time"], int):
                    return False
                if not isinstance(item["stop_time"], str) \
                        and not isinstance(item["stop_time"], int):
                    return False
        return True

    def get_day_time_periods(self, time_periods):
        day_periods = [x for x in time_periods.items()]
        day_time_periods = []
        for day, periods in day_periods:
            period_list = []
            for period in periods:
                if period["start_time"] == "" and period["stop_time"] == "":
                    continue
                try:
                    p, _ = Period.objects.get_or_create(
                        start_time=period["start_time"], stop_time=period["stop_time"]
                    )
                except ValueError:
                    return None
                period_list.append(p)
            day = Day.objects.get(name=day)
            d = DayTimePeriod.objects.create(day=day)
            [d.periods.add(x) for x in period_list]
            d.save()
            day_time_periods.append(d)
        return day_time_periods

    def save_post(self, params, *args, **kwargs):
        # Check advanced required_params
        if not self.check_param_time_periods(params["time_periods"]):
            return JsonResponse({"success": False, "message": "Parameter time_periods is not valid"}, status=400)
        day_time_periods = self.get_day_time_periods(params["time_periods"])
        if not day_time_periods:
            return JsonResponse({"success": False, "message": "stop_time has to be after start_time"})

        # Create TimePeriod
        if TimePeriod.objects.filter(name=params["name"]).exists():
            return JsonResponse(
                {"success": False, "message": "TimePeriod with this name already exists"},
                status=409
            )
        time_period = TimePeriod.objects.create(name=params["name"])
        [time_period.time_periods.add(x) for x in day_time_periods]
        if "comment" in params:
            time_period.comment = params["comment"]
        time_period.save()
        return JsonResponse(
            {"success": True, "message": "TimePeriod was successfully added", "data": time_period.id}
        )

    def save_put(self, params, *args, **kwargs):
        try:
            time_period = TimePeriod.objects.get(id=kwargs["sid"])
        except TimePeriod.DoesNotExist:
            return JsonResponse(
                {"success": False, "message": f"TimePeriod with id {kwargs['sid']} does not exist"},
                status=404
            )
        if "name" in params:
            time_period.name = params["name"]
        if "time_periods" in params:
            if not self.check_param_time_periods(params["time_periods"]):
                return JsonResponse({"success": False, "message": "Parameter time_periods is not valid"}, status=400)
            day_time_periods = self.get_day_time_periods(params["time_periods"])
            if not day_time_periods:
                return JsonResponse({"success": False, "message": "stop_time has to be after start_time"})
            time_period.time_periods.clear()
            [time_period.time_periods.add(x) for x in day_time_periods]
        if "comment" in params:
            time_period.comment = params["comment"]
        time_period.save()
        return JsonResponse({"success": True, "message": "Changes were successful"})


class GlobalVariableView(CheckOptionalMixinView):
    def __init__(self):
        super(GlobalVariableView, self).__init__(
            api_class=GlobalVariable,
            required_post=["key", "value"]
        )

    def optional(self, params, variable):
        if "comment" in params:
            variable.comment = params["comment"]

    def save_post(self, params, *args, **kwargs):
        if GlobalVariable.objects.filter(variable__key__label=params["key"]).exists():
            return JsonResponse(
                {"success": False, "message": f"GlobalVariable with key {params['key']} already exists"},
                status=409
            )
        variable = GlobalVariable.objects.create()
        key_label, _ = Label.objects.get_or_create(label=params["key"])
        value_label, _ = Label.objects.get_or_create(label=params["value"])
        kvp, _ = GenericKVP.objects.get_or_create(
            key=key_label, value=value_label, object_id=variable.id,
            content_type=ContentType.objects.get_for_model(GlobalVariable)
        )
        self.optional(params, variable)
        variable.save()
        return JsonResponse(
            {"success": True, "message": f"GlobalVariable was created successfully", "data": variable.id}
        )

    def save_put(self, params, *args, **kwargs):
        try:
            variable = GlobalVariable.objects.get(id=kwargs['sid'])
        except GlobalVariable.DoesNotExist:
            return JsonResponse(
                {"success": False, "message": f"GlobalVariable with id {kwargs['sid']} does not exist"},
                status=404
            )
        kvp = variable.variable.first()
        if "key" in params:
            if not GlobalVariable.objects.filter(variable__key__label=params["key"]).exists() \
                    or params["key"] == variable.variable.first().key.label:
                key_label, _ = Label.objects.get_or_create(label=params["key"])
                kvp.key = key_label
            else:
                return JsonResponse(
                    {"success": False, "message": f"GlobalVariable with name {params['key']} already exists"},
                    status=409
                )
        if "value" in params:
            value_label, _ = Label.objects.get_or_create(label=params["value"])
            kvp.value = value_label
        self.optional(params, variable)
        kvp.save()
        variable.save()
        return JsonResponse(
            {"success": True, "message": f"GlobalVariable with id {kwargs['sid']} was changed successful"}
        )


class ContactView(CheckOptionalMixinView):
    def __init__(self):
        super(ContactView, self).__init__(
            api_class=Contact,
            required_post=["name"]
        )

    def optional(self, contact, params, overwrite=False):
        if "comment" in params:
            contact.comment = params["comment"]
        if "mail" in params:
            contact.mail = params["mail"]

        for notification in ["linked_host_notifications", "linked_observable_notifications"]:
            if notification in params:
                if params[notification]:
                    if overwrite:
                        contact.__getattribute__(notification).clear()
                    if isinstance(params[notification], list):
                        for x in params[notification]:
                            try:
                                check = Check.objects.get(id=x)
                                contact.__getattribute__(notification).add(check)
                            except Check.DoesNotExist:
                                return JsonResponse(
                                    {"success": False, "message": f"Check with id {x} does not exist"}, status=404
                                )
                    else:
                        try:
                            check = Check.objects.get(id=params[notification])
                            contact.__getattribute__(notification).add(check)
                        except Check.DoesNotExist:
                            return JsonResponse(
                                {
                                    "success": False,
                                    "message": f"Check with id {params['linked_host_notifications']} does not exist"
                                }, status=404
                            )
                else:
                    contact.__getattribute__(notification).clear()
        for period in ["linked_host_notification_period", "linked_observable_notification_period"]:
            if period in params:
                if params[period]:
                    try:
                        tp = TimePeriod.objects.get(id=params[period])
                        contact.__setattr__(period, tp)
                    except TimePeriod.DoesNotExist:
                        return JsonResponse(
                            {"success": False, "message": f"TimePeriod with id {params[period]} does not exist"},
                            status=404
                        )
                else:
                    contact.__setattr__(period, None)
        if "variables" in params:
            if not isinstance(params["variables"], dict) and not isinstance(params["variables"], str):
                return JsonResponse({"success": False, "message": f"Parameter variables has to be a dict"}, status=400)
            if params["variables"]:
                if overwrite:
                    contact.variables.clear()
                for key, value in params["variables"].items():
                    key_label, _ = Label.objects.get_or_create(label=key)
                    value_label, _ = Label.objects.get_or_create(label=value)
                    GenericKVP.objects.get_or_create(
                        key=key_label, value=value_label, object_id=contact.id,
                        content_type=ContentType.objects.get_for_model(Contact)
                    )
            else:
                contact.variables.clear()

    def save_post(self, params, *args, **kwargs):
        if Contact.objects.filter(name=params["name"]).exists():
            return JsonResponse(
                {"success": False, "message": f"Contact with name {params['name']} already exists"}, status=409
            )
        contact = Contact.objects.create(name=params["name"])
        ret = self.optional(contact, params)
        if isinstance(ret, JsonResponse):
            contact.delete()
            return ret
        contact.save()
        return JsonResponse({"success": True, "message": "Contact was successfully created", "data": contact.id})

    def save_put(self, params, *args, **kwargs):
        try:
            contact = Contact.objects.get(id=kwargs["sid"])
        except Contact.DoesNotExist:
            return JsonResponse(
                {"success": False, "message": f"Contact with id {kwargs['sid']} does not exist"}, status=404
            )
        if "name" in params:
            if Contact.objects.filter(name=params["name"]).exists():
                if params["name"] != contact.name:
                    return JsonResponse(
                        {"success": False, "message": "Contact with the name of the parameter name already exists"},
                        status=409
                    )
            contact.name = params["name"]
        ret = self.optional(contact, params, overwrite=True)
        if isinstance(ret, JsonResponse):
            return ret
        contact.save()
        return JsonResponse({"success": True, "message": "Contact was changed successful"})


class ContactGroupView(CheckOptionalMixinView):
    def __init__(self):
        super(ContactGroupView, self).__init__(
            api_class=ContactGroup,
            required_post=["name"]
        )

    def optional(self, contact_group, params, overwrite=False):
        if "linked_contacts" in params:
            if params["linked_contacts"]:
                if overwrite:
                    contact_group.linked_contacts.clear()
                if isinstance(params["linked_contacts"], list):
                    for cid in params["linked_contacts"]:
                        try:
                            contact = Contact.objects.get(id=cid)
                            contact_group.linked_contacts.add(contact)
                        except Contact.DoesNotExist:
                            return JsonResponse(
                                {"success": False, "message": f"Contact with id {cid} does not exist"}, status=404
                            )
                else:
                    try:
                        contact = Contact.objects.get(id=params["linked_contacts"])
                        contact_group.linked_contacts.add(contact)
                    except Contact.DoesNotExist:
                        return JsonResponse(
                            {"success": False,
                             "message": f"Contact with id {params['linked_contacts']} does not exist"},
                            status=404
                        )
            else:
                contact_group.linked_contacts.clear()
        if "comment" in params:
            contact_group.comment = params["comment"]

    def save_post(self, params, *args, **kwargs):
        if ContactGroup.objects.filter(name=params["name"]).exists():
            return JsonResponse(
                {"success": False, "message": f"ContactGroup with name {params['name']} already exists"}, status=409
            )
        contact_group = ContactGroup.objects.create(name=params["name"])

        ret = self.optional(contact_group, params)
        if isinstance(ret, JsonResponse):
            contact_group.delete()
            return ret

        contact_group.save()
        return JsonResponse(
            {"success": True, "message": "ContactGroup was successfully created", "data": contact_group.id}
        )

    def save_put(self, params, *args, **kwargs):
        try:
            contact_group = ContactGroup.objects.get(id=kwargs["sid"])
        except ContactGroup.DoesNotExist:
            return JsonResponse(
                {"success": False, "message": f"ContactGroup with id {kwargs['sid']} does not exist"}, status=404
            )
        if "name" in params:
            contact_group.name = params["name"]

        ret = self.optional(contact_group, params, overwrite=True)
        if isinstance(ret, JsonResponse):
            return ret

        contact_group.save()
        return JsonResponse({"success": True, "message": "Changes were successful"})


class ProxyView(CheckOptionalMixinView):
    def __init__(self):
        super(ProxyView, self).__init__(
            api_class=Proxy,
            required_post=["name", "address", "port", "core_address", "core_port"]
        )

    def optional(self, proxy, params):
        if "disabled" in params:
            if isinstance(params["disabled"], bool):
                proxy.disabled = params["disabled"]
            else:
                return JsonResponse({"success": False, "message": "Parameter disabled has to be a bool"}, status=400)
        if "comment" in params:
            proxy.comment = params["comment"]

    def save_post(self, params, *args, **kwargs):
        if Proxy.objects.filter(name=params["name"]).exists():
            return JsonResponse(
                {"success": False, "message": f"Proxy with name {params['name']} already exists"}, status=409
            )
        alphabet = string.ascii_letters + string.digits
        proxy = Proxy.objects.create(
            name=params["name"],
            address=params["address"],
            port=params["port"],
            secret="".join(secrets.choice(alphabet) for _ in range(255)),
            core_address=params["core_address"],
            core_port=params["core_port"],
            core_secret="".join(secrets.choice(alphabet) for _ in range(255)),
        )
        ret = self.optional(proxy, params)
        if isinstance(ret, JsonResponse):
            proxy.delete()
            return ret

        return JsonResponse(
            {"success": True, "message": "Proxy was created successful", "data": proxy.id}, status=201
        )

    def save_put(self, params, *args, **kwargs):
        try:
            proxy = Proxy.objects.get(id=kwargs["sid"])
        except Proxy.DoesNotExist:
            return JsonResponse(
                {"success": False, "message": f"Proxy with id {kwargs['sid']} does not exist"}, status=404
            )
        if "name" in params:
            proxy.name = params["name"]
        if "address" in params:
            proxy.address = params["address"]
        if "port" in params:
            proxy.port = params["port"]
        if "core_address" in params:
            proxy.core_address = params["core_address"]
        if "core_port" in params:
            proxy.core_port = params["core_port"]

        ret = self.optional(proxy, params)
        if isinstance(ret, JsonResponse):
            return ret

        proxy.save()
        return JsonResponse({"success": True, "message": "Changes were successful"})


class UpdateDeclarationView(CheckMixinView):
    def __init__(self):
        super(UpdateDeclarationView, self).__init__()

    def cleaned_post(self, params, *args, **kwargs):
        if "proxies" not in params:
            proxies = [x.id for x in Proxy.objects.filter(disabled=False)]
            data = export(proxies)
        else:
            data = export(get_variable_list(params["proxies"]))
        return JsonResponse({"success": True, "message": "Request was successful.", "data": data})


class GenerateProxyConfigurationView(CheckMixinView):
    def __init__(self):
        super(GenerateProxyConfigurationView, self).__init__(
            required_post=["proxy"]
        )

    def cleaned_post(self, params, *args, **kwargs):
        try:
            proxy_id = int(params["proxy"])
        except ValueError:
            return JsonResponse({"success": False, "message": "Parameter proxy must be of type int"}, status=400)
        try:
            proxy = Proxy.objects.get(id=proxy_id)
        except Proxy.DoesNotExist:
            return JsonResponse(
                {"success": False, "message": f"Proxy with id {params['proxy']} does not exist"}, status=404
            )
        return JsonResponse({
            "success": True,
            "message": "Request was successful",
            "data": f"/usr/sbin/q-proxy/venv/bin/python3 /usr/sbin/q-proxy/manage.py init --b64 '{proxy.to_base64()}'"
        })
