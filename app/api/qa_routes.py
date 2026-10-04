from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.api.schemas import (
    BugCreateRequest,
    BugTransitionRequest,
    EpicCreateRequest,
    EpicMembersRequest,
    FixCreateRequest,
    FixTransitionRequest,
    ProgramImportRequest,
    StoryCreateRequest,
    TaskAssignRequest,
    TaskCreateRequest,
    TaskTransitionRequest,
    TestCaseCreateRequest,
    TestCaseDefinitionUpdateRequest,
)
from app.core.mailer import Mailer, get_mailer
from app.core.security import AuthenticatedUser, require_roles
from app.db.models import User, UserRole
from app.db.session import get_db
from app.domains.qa.mongo import QaDatabase, QaHttpClient
from app.domains.qa.service import (
    QaConflictError,
    QaForbiddenError,
    QaNotFoundError,
    QaValidationError,
    assign_task,
    create_bug,
    create_epic,
    create_fix,
    create_story,
    create_task,
    create_test_case,
    import_program,
    execute_test_case,
    list_case_executions,
    list_epic_bugs,
    retry_pending_notifications,
    transition_bug,
    transition_fix,
    transition_task,
    update_epic_members,
    update_test_case_definition,
)

router = APIRouter(prefix="/qa", tags=["gestion-qa"])
DbSession = Annotated[Session, Depends(get_db)]
MailerDependency = Annotated[Mailer, Depends(get_mailer)]


def _raise_http(error: Exception) -> None:
    if isinstance(error, QaNotFoundError):
        raise HTTPException(status_code=404, detail="No se encontro el elemento solicitado.")
    if isinstance(error, QaForbiddenError):
        raise HTTPException(status_code=403, detail="No tienes permisos para esta accion.")
    if isinstance(error, QaConflictError):
        raise HTTPException(status_code=409, detail="El elemento no permite ese cambio de estado.")
    if isinstance(error, QaValidationError):
        raise HTTPException(status_code=422, detail=str(error))
    raise error


@router.post("/epics", status_code=status.HTTP_201_CREATED)
def post_epic(
    payload: EpicCreateRequest,
    database: QaDatabase,
    db: DbSession,
    actor: AuthenticatedUser,
    mailer: MailerDependency,
) -> dict:
    try:
        return create_epic(
            database,
            db,
            title=payload.title,
            description=payload.description,
            member_ids=payload.member_ids,
            actor=actor,
            mailer=mailer,
        )
    except (QaNotFoundError, QaForbiddenError, QaConflictError, QaValidationError) as error:
        _raise_http(error)


@router.get("/epics")
def get_epics(database: QaDatabase, actor: AuthenticatedUser) -> list[dict]:
    return [
        {
            key: value
            for key, value in epic.items()
            if key not in {"_id", "notifications"}
        }
        for epic in database.epics.find({"members.user_id": actor.id}).sort(
            "created_at_epoch", -1
        )
    ]


@router.put("/epics/{epic_key}/members")
def put_epic_members(
    epic_key: str,
    payload: EpicMembersRequest,
    database: QaDatabase,
    db: DbSession,
    actor: Annotated[
        User,
        Depends(require_roles(UserRole.ADMIN, UserRole.ADMINISTRADOR)),
    ],
    mailer: MailerDependency,
) -> dict:
    try:
        return update_epic_members(
            database,
            db,
            epic_key=epic_key,
            member_ids=payload.member_ids,
            actor=actor,
            mailer=mailer,
        )
    except (QaNotFoundError, QaForbiddenError, QaConflictError, QaValidationError) as error:
        _raise_http(error)


@router.post("/epics/{epic_key}/stories", status_code=status.HTTP_201_CREATED)
def post_story(
    epic_key: str,
    payload: StoryCreateRequest,
    database: QaDatabase,
    actor: Annotated[User, Depends(require_roles(UserRole.ADMINISTRADOR))],
    mailer: MailerDependency,
) -> dict:
    try:
        return create_story(
            database,
            epic_key=epic_key,
            actor=actor,
            mailer=mailer,
            title=payload.title,
            description=payload.description,
            priority=payload.priority,
            acceptance_criteria=payload.acceptance_criteria,
        )
    except (QaNotFoundError, QaForbiddenError, QaConflictError, QaValidationError) as error:
        _raise_http(error)


@router.post("/epics/{epic_key}/import", status_code=status.HTTP_201_CREATED)
def post_program_import(
    epic_key: str,
    payload: ProgramImportRequest,
    database: QaDatabase,
    actor: AuthenticatedUser,
    mailer: MailerDependency,
) -> dict:
    try:
        return import_program(
            database,
            epic_key=epic_key,
            actor=actor,
            mailer=mailer,
            program=payload.model_dump(),
        )
    except (QaNotFoundError, QaForbiddenError, QaConflictError, QaValidationError) as error:
        _raise_http(error)


@router.get("/epics/{epic_key}/work-items")
def get_epic_work_items(
    epic_key: str,
    database: QaDatabase,
    actor: AuthenticatedUser,
) -> list[dict]:
    try:
        epic = database.epics.find_one({"key": epic_key, "kind": "epic"})
        if epic is None:
            raise QaNotFoundError
        if actor.id not in {member["user_id"] for member in epic["members"]}:
            raise QaForbiddenError
        return [
            {
                key: value
                for key, value in item.items()
                if key not in {"_id", "notifications"}
            }
            for item in database.work_items.find({"epic_key": epic_key}).sort(
                "created_at_epoch", 1
            )
        ]
    except (QaNotFoundError, QaForbiddenError) as error:
        _raise_http(error)


@router.post(
    "/stories/{story_key}/test-cases",
    status_code=status.HTTP_201_CREATED,
)
def post_test_case(
    story_key: str,
    payload: TestCaseCreateRequest,
    database: QaDatabase,
    actor: Annotated[User, Depends(require_roles(UserRole.ADMINISTRADOR))],
    mailer: MailerDependency,
) -> dict:
    try:
        return create_test_case(
            database,
            story_key=story_key,
            actor=actor,
            mailer=mailer,
            title=payload.title,
            description=payload.description,
            priority=payload.priority,
            preconditions=payload.preconditions,
            steps=[step.model_dump() for step in payload.steps],
            expected_result=payload.expected_result,
            request_method=payload.request_method,
            request_path=payload.request_path,
            request_query=payload.request_query,
            request_headers=payload.request_headers,
            request_body=payload.request_body,
            expected_status_codes=payload.expected_status_codes,
            expected_response=payload.expected_response,
        )
    except (QaNotFoundError, QaForbiddenError, QaConflictError, QaValidationError) as error:
        _raise_http(error)


@router.post(
    "/epics/{epic_key}/tasks",
    status_code=status.HTTP_201_CREATED,
)
def post_task(
    epic_key: str,
    payload: TaskCreateRequest,
    database: QaDatabase,
    actor: Annotated[
        User, Depends(require_roles(UserRole.ADMIN, UserRole.ADMINISTRADOR))
    ],
    mailer: MailerDependency,
) -> dict:
    try:
        return create_task(
            database,
            epic_key=epic_key,
            actor=actor,
            mailer=mailer,
            title=payload.title,
            description=payload.description,
            assignee_id=payload.assignee_id,
        )
    except (QaNotFoundError, QaForbiddenError, QaConflictError, QaValidationError) as error:
        _raise_http(error)


@router.post("/tasks/{task_key}/assign")
def post_task_assignment(
    task_key: str,
    payload: TaskAssignRequest,
    database: QaDatabase,
    actor: AuthenticatedUser,
    mailer: MailerDependency,
) -> dict:
    try:
        return assign_task(
            database,
            task_key=task_key,
            assignee_id=payload.assignee_id,
            actor=actor,
            mailer=mailer,
        )
    except (QaNotFoundError, QaForbiddenError, QaConflictError, QaValidationError) as error:
        _raise_http(error)


@router.put("/cases/{case_key}")
def put_case_definition(
    case_key: str,
    payload: TestCaseDefinitionUpdateRequest,
    database: QaDatabase,
    actor: AuthenticatedUser,
    mailer: MailerDependency,
) -> dict:
    try:
        return update_test_case_definition(
            database,
            case_key=case_key,
            actor=actor,
            mailer=mailer,
            request_method=payload.request_method,
            request_path=payload.request_path,
            request_query=payload.request_query,
            request_headers=payload.request_headers,
            request_body=payload.request_body,
            expected_status_codes=payload.expected_status_codes,
            expected_response=payload.expected_response,
            change_note=payload.change_note,
        )
    except (QaNotFoundError, QaForbiddenError, QaConflictError, QaValidationError) as error:
        _raise_http(error)


@router.post("/tasks/{task_key}/transition")
def post_task_transition(
    task_key: str,
    payload: TaskTransitionRequest,
    database: QaDatabase,
    actor: AuthenticatedUser,
    mailer: MailerDependency,
) -> dict:
    try:
        return transition_task(
            database,
            task_key=task_key,
            target_status=payload.status,
            actor=actor,
            mailer=mailer,
        )
    except (QaNotFoundError, QaForbiddenError, QaConflictError, QaValidationError) as error:
        _raise_http(error)


@router.post("/cases/{case_key}/execute", status_code=status.HTTP_201_CREATED)
def post_case_execution(
    case_key: str,
    database: QaDatabase,
    request: Request,
    actor: AuthenticatedUser,
    mailer: MailerDependency,
    client: QaHttpClient,
) -> dict:
    try:
        execution = execute_test_case(
            database,
            case_key=case_key,
            actor=actor,
            mailer=mailer,
            client=client,
            cookies=dict(request.cookies),
            csrf_token=request.headers.get("x-csrf-token"),
            incoming_host=request.headers.get("host"),
        )
        return execution
    except (QaNotFoundError, QaForbiddenError, QaConflictError, QaValidationError) as error:
        _raise_http(error)


@router.get("/cases/{case_key}/executions")
def get_case_executions(
    case_key: str,
    database: QaDatabase,
    actor: AuthenticatedUser,
) -> list[dict]:
    try:
        return list_case_executions(database, case_key=case_key, actor=actor)
    except (QaNotFoundError, QaForbiddenError) as error:
        _raise_http(error)


@router.get("/epics/{epic_key}/bugs")
def get_epic_bugs(
    epic_key: str,
    database: QaDatabase,
    actor: AuthenticatedUser,
) -> list[dict]:
    try:
        return list_epic_bugs(database, epic_key=epic_key, actor=actor)
    except (QaNotFoundError, QaForbiddenError) as error:
        _raise_http(error)


@router.post("/bugs", status_code=status.HTTP_201_CREATED)
def post_bug(
    payload: BugCreateRequest,
    database: QaDatabase,
    actor: Annotated[User, Depends(require_roles(UserRole.USUARIO))],
    mailer: MailerDependency,
) -> dict:
    try:
        return create_bug(
            database,
            case_key=payload.case_key,
            execution_key=payload.execution_key,
            actor=actor,
            mailer=mailer,
            title=payload.title,
            description=payload.description,
            severity=payload.severity,
        )
    except (QaNotFoundError, QaForbiddenError, QaConflictError, QaValidationError) as error:
        _raise_http(error)


@router.post("/bugs/{bug_key}/fixes", status_code=status.HTTP_201_CREATED)
def post_fix(
    bug_key: str,
    payload: FixCreateRequest,
    database: QaDatabase,
    actor: Annotated[User, Depends(require_roles(UserRole.USUARIO))],
    mailer: MailerDependency,
) -> dict:
    try:
        return create_fix(
            database,
            bug_key=bug_key,
            actor=actor,
            mailer=mailer,
            title=payload.title,
            description=payload.description,
        )
    except (QaNotFoundError, QaForbiddenError, QaConflictError, QaValidationError) as error:
        _raise_http(error)


@router.post("/bugs/{bug_key}/fixes/{fix_key}/transition")
def post_fix_transition(
    bug_key: str,
    fix_key: str,
    _: FixTransitionRequest,
    database: QaDatabase,
    actor: Annotated[User, Depends(require_roles(UserRole.USUARIO))],
    mailer: MailerDependency,
) -> dict:
    try:
        return transition_fix(
            database,
            bug_key=bug_key,
            fix_key=fix_key,
            actor=actor,
            mailer=mailer,
        )
    except (QaNotFoundError, QaForbiddenError, QaConflictError, QaValidationError) as error:
        _raise_http(error)


@router.post("/bugs/{bug_key}/transition")
def post_bug_transition(
    bug_key: str,
    payload: BugTransitionRequest,
    database: QaDatabase,
    # El servicio decide: el usuario avanza el flujo; solo gestión asigna responsables.
    actor: AuthenticatedUser,
    mailer: MailerDependency,
) -> dict:
    try:
        return transition_bug(
            database,
            bug_key=bug_key,
            target_status=payload.status,
            retest_passed=payload.retest_passed,
            assignee_id=payload.assignee_id,
            actor=actor,
            mailer=mailer,
        )
    except (QaNotFoundError, QaForbiddenError, QaConflictError, QaValidationError) as error:
        _raise_http(error)


@router.post("/notifications/retry")
def post_retry_notifications(
    database: QaDatabase,
    actor: Annotated[
        User,
        Depends(require_roles(UserRole.ADMIN, UserRole.ADMINISTRADOR)),
    ],
    mailer: MailerDependency,
) -> dict[str, int]:
    try:
        return retry_pending_notifications(database, actor=actor, mailer=mailer)
    except (QaNotFoundError, QaForbiddenError, QaConflictError, QaValidationError) as error:
        _raise_http(error)
