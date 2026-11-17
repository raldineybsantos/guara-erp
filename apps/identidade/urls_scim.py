# Guará ERP — gestão empresarial livre
# Copyright (C) 2026 Raldiney Santos e colaboradores
# SPDX-License-Identifier: GPL-3.0-or-later
from django.urls import path

from . import scim

urlpatterns = [
    path("Users", scim.usuarios),
    path("Users/<int:pk>", scim.usuario),
    path("Groups", scim.grupos),
    path("ServiceProviderConfig", scim.config),
    path("ResourceTypes", scim.tipos),
]
