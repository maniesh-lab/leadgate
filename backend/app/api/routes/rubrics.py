"""Scoring rubrics (`/api/rubrics`): list and read them; admins add and edit them.

Rubrics are never changed in place. Editing one (`POST /api/rubrics/{id}`) saves the
rules as the next version under the same name, so a campaign or run that points to the
old version still scores the same way.
"""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import AdminUser, CurrentUser, DbSession, Paging, get_or_404, paginate
from app.api.errors import error_docs
from app.db.models import ActorType, ScoringRubric, User
from app.schemas.common import Page
from app.schemas.rubrics import RubricCreate, RubricEdit, RubricOut, Rule
from app.services.audit import record

router = APIRouter(prefix="/rubrics", tags=["rubrics"], responses=error_docs(401))


@router.get("")
def list_rubrics(
    _user: CurrentUser,
    db: DbSession,
    paging: Paging,
    name: Annotated[str | None, Query(description="only this rubric's versions")] = None,
) -> Page[RubricOut]:
    """Every rubric version, by name, newest version first."""
    stmt = select(ScoringRubric).order_by(ScoringRubric.name, ScoringRubric.version.desc())
    if name is not None:
        stmt = stmt.where(ScoringRubric.name == name)
    rows, total = paginate(db, stmt, paging)
    return Page(
        items=[RubricOut.model_validate(rubric) for (rubric,) in rows],
        total=total,
        page=paging.page,
        page_size=paging.page_size,
    )


@router.get("/{rubric_id}", responses=error_docs(404))
def get_rubric(rubric_id: int, _user: CurrentUser, db: DbSession) -> RubricOut:
    return RubricOut.model_validate(get_or_404(db, ScoringRubric, rubric_id, "rubric"))


@router.post("", status_code=status.HTTP_201_CREATED, responses=error_docs(403, 409))
def create_rubric(body: RubricCreate, admin: AdminUser, db: DbSession) -> RubricOut:
    """A new rubric (version 1). 409 if the name is taken: edit that rubric instead."""
    if db.scalar(select(ScoringRubric.id).where(ScoringRubric.name == body.name)) is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"rubric {body.name!r} exists; edit it for a new version"
        )
    return save_version(db, admin, body.name, 1, body.rules)


@router.post(
    "/{rubric_id}", status_code=status.HTTP_201_CREATED, responses=error_docs(403, 404, 409)
)
def edit_rubric(rubric_id: int, body: RubricEdit, admin: AdminUser, db: DbSession) -> RubricOut:
    """Save new rules as the next version of this rubric (the old version stays)."""
    rubric = get_or_404(db, ScoringRubric, rubric_id, "rubric")
    latest = db.scalar(
        select(func.max(ScoringRubric.version)).where(ScoringRubric.name == rubric.name)
    )
    return save_version(db, admin, rubric.name, latest + 1, body.rules)


def save_version(db: Session, admin: User, name: str, version: int, rules: list[Rule]) -> RubricOut:
    rubric = ScoringRubric(
        name=name, version=version, rules=[rule.model_dump() for rule in rules], created_by=admin.id
    )
    db.add(rubric)
    try:
        db.flush()
    except IntegrityError as exc:  # someone saved the same version a moment ago
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"rubric {name!r} v{version} was just saved; try again"
        ) from exc
    record(
        db,
        actor_type=ActorType.USER,
        actor=admin.email,
        action="rubric.created",
        entity_type="rubric",
        entity_id=rubric.id,
        details={"name": name, "version": version, "rules": len(rules)},
    )
    db.commit()
    return RubricOut.model_validate(rubric)
