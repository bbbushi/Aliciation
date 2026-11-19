"""Design-v2 contracts, adapters and services, wired through ChatService.

See docs/设计文档/自主探索与主动交流/自主活动质量与邀请式交流-骨架交接-20261117.md.
Importing this package still migrates nothing and starts no workers; the
ChatService constructs the stack lazily under its own lock.
"""
from __future__ import annotations


def build(service=None, store=None):
    """Assemble the v2 stack over one Store. Callers hold the service lock.

    ``service`` is preferred (host delivery uses its Store); a bare ``store``
    is enough for read paths and foreground tool dispatch.
    """
    from .adapters import (
        ActivityRepositoryAdapter, HostAdapter, InvitationRepositoryAdapter,
        MigrationAdapter, StoreEvidence, SystemClock, ViewRepositoryAdapter,
    )
    from .services import (
        ActivityScheduler, AutonomyV2, ForegroundAssembler, InvitationService,
        ProgressService, ViewService, WaitingService,
    )
    from .models import ModePolicy, Tuning
    from .communication import CommunicationService
    from .tuning import tuning_from_settings

    target_store = store if store is not None else service.store
    activities = ActivityRepositoryAdapter(target_store)
    views = ViewRepositoryAdapter(target_store)
    invitations = InvitationRepositoryAdapter(target_store)
    evidence = StoreEvidence(target_store)
    host = HostAdapter(target_store)
    clock = SystemClock()
    tuning = tuning_from_settings(target_store)
    scheduler = ActivityScheduler(repository=activities, evidence=evidence, clock=clock,
                                  host=host, tuning=tuning)
    progress = ProgressService(repository=activities, evidence=evidence, host=host, tuning=tuning)
    waiting = WaitingService(repository=activities, evidence=evidence)
    view_service = ViewService(repository=views, questions=activities, evidence=evidence)
    invitation_service = InvitationService(repository=invitations, host=host, clock=clock, tuning=tuning)
    foreground = ForegroundAssembler(views=view_service, questions=activities,
                                     invitations=invitations, host=host)
    return AutonomyV2(scheduler=scheduler, progress=progress, waiting=waiting,
                      views=view_service, invitations=invitation_service, foreground=foreground,
                      repository=activities, migration=MigrationAdapter(target_store),
                      communication=CommunicationService(invitations=invitation_service))
