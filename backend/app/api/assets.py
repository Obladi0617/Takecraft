"""角色 / 场景资产 API（规格书第 13、14 节）。

用户确认链路：DRAFT →（生成三视图 / 参考图）→ PENDING_CONFIRM → LOCKED。
锁定瞬间编译 prompt_block 并固化 hash，之后所有镜头提示词逐字复用。
"""

from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlmodel import Session, select

from ..compliance import compliance_gate
from ..db import project_session
from ..domain import Character, Location, Scene
from ..domain.character import CHARACTER_SOURCES, TURNAROUND_VIEWS
from ..domain.location import LOCATION_REF_KINDS, LOCATION_SOURCES
from ..repositories import next_seq_and_id
from ..services.assets import (
    CHARACTER_ASSET_TYPE,
    LOCATION_ASSET_TYPE,
    asset_media_url,
    character_design_prompt,
    enqueue_character_turnaround,
    enqueue_location_refs,
    link_shots_to_assets,
    location_design_prompt,
    lock_character,
    lock_location,
    refs_of,
    stable_seed,
    store_uploaded_asset,
)

router = APIRouter(prefix="/api/v1/projects/{project_id}", tags=["assets"])


class CharacterIn(BaseModel):
    name: str
    age_range: str | None = None
    gender: str | None = None
    role: str = "PRIMARY"
    description: str = ""
    appearance: str = ""
    costume: str = ""
    personality: str = ""
    visual_anchors: list[str] = []
    immutable_traits: list[str] = []
    source: str = "AUTO"


class CharacterPatch(BaseModel):
    name: str | None = None
    age_range: str | None = None
    gender: str | None = None
    role: str | None = None
    description: str | None = None
    appearance: str | None = None
    costume: str | None = None
    personality: str | None = None
    visual_anchors: list[str] | None = None
    immutable_traits: list[str] | None = None


class LocationIn(BaseModel):
    name: str
    scene_id: str | None = None
    description: str = ""
    visual_style: str = ""
    time_of_day_default: str = ""
    materials: list[str] = []
    colors: list[str] = []
    visual_cues: list[str] = []
    immutable_elements: list[str] = []
    lighting_rules: list[str] = []
    source: str = "AUTO"


class LocationPatch(BaseModel):
    name: str | None = None
    scene_id: str | None = None
    description: str | None = None
    visual_style: str | None = None
    time_of_day_default: str | None = None
    materials: list[str] | None = None
    colors: list[str] | None = None
    visual_cues: list[str] | None = None
    immutable_elements: list[str] | None = None
    lighting_rules: list[str] | None = None


def _refs(session: Session, project_id: str, owner_id: str) -> list[dict]:
    return [
        {
            **a.model_dump(),
            "view": a.meta.get("view"),
            "media_url": asset_media_url(project_id, a),
        }
        for a in refs_of(session, project_id, owner_id)
    ]


def _get_character(session: Session, project_id: str, character_id: str) -> Character:
    character = session.get(Character, character_id)
    if character is None or character.project_id != project_id:
        raise HTTPException(status_code=404, detail="character not found")
    return character


def _get_location(session: Session, project_id: str, location_id: str) -> Location:
    location = session.get(Location, location_id)
    if location is None or location.project_id != project_id:
        raise HTTPException(status_code=404, detail="location not found")
    return location


def _assert_editable(owner: Character | Location) -> None:
    if owner.status == "LOCKED":
        raise HTTPException(status_code=409, detail="资产已锁定，请先重新生成参考图后再确认")


@router.get("/assets")
def list_assets(project_id: str, session: Session = Depends(project_session)):
    characters = list(
        session.exec(
            select(Character)
            .where(Character.project_id == project_id)
            .order_by(Character.index)
        )
    )
    locations = list(
        session.exec(
            select(Location)
            .where(Location.project_id == project_id)
            .order_by(Location.index)
        )
    )
    return {
        "characters": [
            {
                **c.model_dump(),
                "references": _refs(session, project_id, c.id),
                "views": list(TURNAROUND_VIEWS),
            }
            for c in characters
        ],
        "locations": [
            {
                **loc.model_dump(),
                "references": _refs(session, project_id, loc.id),
                "views": list(LOCATION_REF_KINDS),
            }
            for loc in locations
        ],
    }


@router.post("/assets/link-shots")
def relink_shots(project_id: str, session: Session = Depends(project_session)):
    return {"links": link_shots_to_assets(session, project_id)}


@router.post("/characters", status_code=201)
def create_character(
    project_id: str,
    body: CharacterIn,
    session: Session = Depends(project_session),
):
    compliance_gate.check_or_raise(
        "，".join(filter(None, [body.name, body.description, body.appearance])),
        "character.create",
        project_id,
    )
    if body.source not in CHARACTER_SOURCES:
        raise HTTPException(status_code=400, detail=f"source 必须是 {CHARACTER_SOURCES}")
    index, char_id = next_seq_and_id(session, Character, project_id, "char")
    character = Character(
        id=char_id,
        project_id=project_id,
        index=index,
        name=body.name,
        age_range=body.age_range,
        gender=body.gender,
        role=body.role,
        description=body.description,
        appearance=body.appearance,
        costume=body.costume,
        personality=body.personality,
        visual_anchors=body.visual_anchors,
        immutable_traits=body.immutable_traits,
        seed=stable_seed(char_id),
        source=body.source,
        status="DRAFT",
    )
    session.add(character)
    session.commit()
    session.refresh(character)
    return {**character.model_dump(), "references": []}


@router.patch("/characters/{character_id}")
def update_character(
    project_id: str,
    character_id: str,
    body: CharacterPatch,
    session: Session = Depends(project_session),
):
    character = _get_character(session, project_id, character_id)
    _assert_editable(character)
    for key, value in body.model_dump(exclude_unset=True).items():
        setattr(character, key, value)
    compliance_gate.check_or_raise(
        "，".join(filter(None, [character.name, character.description, character.appearance])),
        "character.update",
        project_id,
    )
    session.add(character)
    session.commit()
    session.refresh(character)
    return {
        **character.model_dump(),
        "references": _refs(session, project_id, character.id),
    }


@router.post("/characters/{character_id}/references/generate", status_code=202)
def generate_character_refs(
    project_id: str,
    character_id: str,
    session: Session = Depends(project_session),
):
    character = _get_character(session, project_id, character_id)
    prompt = character_design_prompt(character)
    compliance_gate.check_or_raise(prompt, "character.turnaround", project_id)
    job = enqueue_character_turnaround(session, project_id, character, prompt)
    return {"job": job.model_dump()}


@router.post("/characters/{character_id}/references")
async def upload_character_ref(
    project_id: str,
    character_id: str,
    file: UploadFile,
    view: str = Form("FRONT"),
    session: Session = Depends(project_session),
):
    character = _get_character(session, project_id, character_id)
    if view not in TURNAROUND_VIEWS:
        raise HTTPException(status_code=400, detail=f"view 必须是 {TURNAROUND_VIEWS}")
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="空文件")
    asset = store_uploaded_asset(
        session,
        project_id,
        character.id,
        CHARACTER_ASSET_TYPE,
        view,
        file.filename or "reference.png",
        data,
    )
    # 先算好返回体：下面的状态回退会 commit，届时 asset 已 expire，model_dump() 会变 {}
    payload = asset.model_dump()
    payload["view"] = view
    payload["media_url"] = asset_media_url(project_id, asset)
    # 参考图已换，旧的 prompt_block/hash 不再可信：退回待确认让用户重新锁定
    if character.status == "LOCKED":
        character.status = "PENDING_CONFIRM"
        session.add(character)
        session.commit()
    return payload


@router.post("/characters/{character_id}/lock")
def lock_character_api(
    project_id: str,
    character_id: str,
    session: Session = Depends(project_session),
):
    character = _get_character(session, project_id, character_id)
    locked = lock_character(session, character)
    # 必须先取快照：link_shots_to_assets 内部 commit 会让实例 expire，
    # 而 model_dump() 不走 SQLAlchemy 的加载描述符，过期后会静默返回 {}
    payload = locked.model_dump()
    link_shots_to_assets(session, project_id)
    return {
        **payload,
        "references": _refs(session, project_id, payload["id"]),
    }


@router.post("/locations", status_code=201)
def create_location(
    project_id: str,
    body: LocationIn,
    session: Session = Depends(project_session),
):
    compliance_gate.check_or_raise(
        "，".join(filter(None, [body.name, body.description, body.visual_style])),
        "location.create",
        project_id,
    )
    if body.source not in LOCATION_SOURCES:
        raise HTTPException(status_code=400, detail=f"source 必须是 {LOCATION_SOURCES}")
    if body.scene_id and session.get(Scene, body.scene_id) is None:
        raise HTTPException(status_code=404, detail="scene not found")
    index, loc_id = next_seq_and_id(session, Location, project_id, "loc")
    location = Location(
        id=loc_id,
        project_id=project_id,
        index=index,
        scene_id=body.scene_id,
        name=body.name,
        description=body.description,
        visual_style=body.visual_style,
        time_of_day_default=body.time_of_day_default,
        materials=body.materials,
        colors=body.colors,
        visual_cues=body.visual_cues,
        immutable_elements=body.immutable_elements,
        lighting_rules=body.lighting_rules,
        seed=stable_seed(loc_id),
        source=body.source,
        status="DRAFT",
    )
    session.add(location)
    session.commit()
    session.refresh(location)
    return {**location.model_dump(), "references": []}


@router.patch("/locations/{location_id}")
def update_location(
    project_id: str,
    location_id: str,
    body: LocationPatch,
    session: Session = Depends(project_session),
):
    location = _get_location(session, project_id, location_id)
    _assert_editable(location)
    for key, value in body.model_dump(exclude_unset=True).items():
        setattr(location, key, value)
    compliance_gate.check_or_raise(
        "，".join(filter(None, [location.name, location.description, location.visual_style])),
        "location.update",
        project_id,
    )
    session.add(location)
    session.commit()
    session.refresh(location)
    return {
        **location.model_dump(),
        "references": _refs(session, project_id, location.id),
    }


@router.post("/locations/{location_id}/references/generate", status_code=202)
def generate_location_refs(
    project_id: str,
    location_id: str,
    session: Session = Depends(project_session),
):
    location = _get_location(session, project_id, location_id)
    prompt = location_design_prompt(location)
    compliance_gate.check_or_raise(prompt, "location.reference", project_id)
    job = enqueue_location_refs(session, project_id, location, prompt)
    return {"job": job.model_dump()}


@router.post("/locations/{location_id}/references")
async def upload_location_ref(
    project_id: str,
    location_id: str,
    file: UploadFile,
    view: str = Form("ESTABLISHING"),
    session: Session = Depends(project_session),
):
    location = _get_location(session, project_id, location_id)
    if view not in LOCATION_REF_KINDS:
        raise HTTPException(status_code=400, detail=f"view 必须是 {LOCATION_REF_KINDS}")
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="空文件")
    asset = store_uploaded_asset(
        session,
        project_id,
        location.id,
        LOCATION_ASSET_TYPE,
        view,
        file.filename or "reference.png",
        data,
    )
    # 先算好返回体：下面的状态回退会 commit，届时 asset 已 expire，model_dump() 会变 {}
    payload = asset.model_dump()
    payload["view"] = view
    payload["media_url"] = asset_media_url(project_id, asset)
    # 参考图已换，旧的 prompt_block/hash 不再可信：退回待确认让用户重新锁定
    if location.status == "LOCKED":
        location.status = "PENDING_CONFIRM"
        session.add(location)
        session.commit()
    return payload


@router.post("/locations/{location_id}/lock")
def lock_location_api(
    project_id: str,
    location_id: str,
    session: Session = Depends(project_session),
):
    location = _get_location(session, project_id, location_id)
    locked = lock_location(session, location)
    # 同角色锁定：先取快照，避免 link 内部 commit 后 model_dump() 返回 {}
    payload = locked.model_dump()
    link_shots_to_assets(session, project_id)
    return {
        **payload,
        "references": _refs(session, project_id, payload["id"]),
    }
