"""角色 / 场景资产服务：入队生成、落库参考图、锁定、Shot 自动引用、提示词锚点组装。

规格书第 13/14 节。资产图存于 assets/characters、assets/locations，
Asset 行 id 采用 `{owner_id}_{view}` 确定性主键，重新生成即覆盖。
"""

import hashlib
import shutil
from pathlib import Path

from sqlmodel import Session, select

from ..db import abs_path, project_path
from ..domain import Asset, Character, GenerationJob, Location, Scene, Shot
from ..domain.base import utcnow
from ..domain.character import TURNAROUND_VIEWS
from ..domain.location import LOCATION_REF_KINDS
from ..jobs import generation_queue
from ..repositories import next_seq_and_id

CHARACTER_SUBDIR = "assets/characters"
LOCATION_SUBDIR = "assets/locations"
CHARACTER_ASSET_TYPE = "CHARACTER_TURNAROUND"
LOCATION_ASSET_TYPE = "LOCATION_REFERENCE"

VIEW_LABELS = {
    "FRONT": "严格正面完整主体视图，主体朝向镜头，完整展示整体结构",
    "SIDE": "严格九十度侧面完整主体视图，仅展示侧面轮廓，禁止正面构图",
    "BACK": "严格背面完整主体视图，主体背向镜头，清楚展示背部结构",
    "ESTABLISHING": "场景主角度全景，广角建立镜头，展示完整空间布局",
    "KEY_ANGLE_A": "场景角度 A，从主角度横向改变约四十五度，展示明显不同的空间透视",
    "KEY_ANGLE_B": "场景反向角度，展示与主角度明显不同的空间关系",
    "DETAIL": "场景细节特写，仅聚焦关键材质、道具和纹理，禁止重复全景构图",
}


def view_prompt(base: str, view: str) -> str:
    return (
        f"{base}，{VIEW_LABELS.get(view, view)}。"
        "必须严格遵守当前视角类型，并与同组其他视角在构图和景别上可明显区分。"
    )


MAX_CHARACTERS_PER_SHOT = 3
# 参考图上限：DashScope wan2.5-i2i 接受 ≤3 张，Phantom ≤4 张，取更严的 3
MAX_REFERENCE_IMAGES = 3
REF_VIEW_PRIORITY = {"FRONT": 0, "ANCHOR": 0, "ESTABLISHING": 1}


def asset_media_url(project_id: str, asset: Asset) -> str:
    # Generated references overwrite a deterministic path. Include the actual
    # file revision so browsers do not keep showing the previous image.
    path = abs_path(project_id, asset.path)
    revision = path.stat().st_mtime_ns if path.exists() else 0
    return f"/media/{project_id}/{asset.path}?v={revision}"


def refs_of(session: Session, project_id: str, owner_id: str) -> list[Asset]:
    """某个角色 / 场景资产的全部参考图（按创建顺序）。"""
    rows = session.exec(
        select(Asset).where(
            Asset.project_id == project_id,
            Asset.type.in_({CHARACTER_ASSET_TYPE, LOCATION_ASSET_TYPE}),
        )
    )
    return [a for a in rows if a.meta.get("owner_id") == owner_id]


def character_design_prompt(character: Character) -> str:
    """三视图基底提示词；队列会再追加 FRONT/SIDE/BACK 视角标签。"""
    parts = [character.name or "角色"]
    if character.appearance:
        parts.append(character.appearance)
    if character.costume:
        parts.append(character.costume)
    if character.immutable_traits:
        parts.append("、".join(character.immutable_traits))
    parts.append("角色设定图，单个完整主体，纯色浅灰背景，均匀柔和光照")
    if character.gender in {"其他", "非人", "无"}:
        parts.append("严格非人类生物设计，禁止出现人类、人物、人形躯干、人脸、人类四肢或服装")
    return "，".join(p for p in parts if p)


def location_design_prompt(location: Location) -> str:
    """场景参考图基底提示词；队列会再追加 ESTABLISHING/KEY_ANGLE/DETAIL 标签。"""
    parts = [location.name or "场景"]
    if location.visual_style:
        parts.append(location.visual_style)
    if location.colors:
        parts.append("主色 " + "、".join(location.colors))
    if location.materials:
        parts.append("材质 " + "、".join(location.materials))
    if location.immutable_elements:
        parts.append("固定元素 " + "、".join(location.immutable_elements))
    if location.lighting_rules:
        parts.append("光线 " + "、".join(location.lighting_rules))
    parts.append("无人空镜，电影感构图")
    return "，".join(p for p in parts if p)


def _str_list(value: object, limit: int = 6) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(v) for v in value if str(v).strip()][:limit]


def create_assets_from_cards(
    session: Session, project_id: str, char_cards: list[dict], loc_cards: list[dict]
) -> tuple[list[Character], list[Location]]:
    """把角色卡 / 场景卡落库（规格书第 13.1、14 节的数据结构）。"""
    characters: list[Character] = []
    for card in char_cards[:3]:
        index, char_id = next_seq_and_id(session, Character, project_id, "char")
        character = Character(
            id=char_id,
            project_id=project_id,
            index=index,
            name=str(card.get("name") or f"角色{index}"),
            age_range=card.get("age_range") or None,
            gender=card.get("gender") or None,
            role=str(card.get("role") or ("PRIMARY" if index == 1 else "SUPPORTING")),
            description=str(card.get("description") or ""),
            appearance=str(card.get("appearance") or ""),
            costume=str(card.get("costume") or ""),
            personality=str(card.get("personality") or ""),
            visual_anchors=_str_list(card.get("visual_anchors")),
            immutable_traits=_str_list(card.get("immutable_traits")),
            seed=stable_seed(char_id),
            source="AUTO",
            status="DRAFT",
        )
        session.add(character)
        characters.append(character)
    session.commit()

    scene_by_title = {
        s.title: s.id
        for s in session.exec(select(Scene).where(Scene.project_id == project_id))
    }
    locations: list[Location] = []
    for card in loc_cards[:3]:
        index, loc_id = next_seq_and_id(session, Location, project_id, "loc")
        location = Location(
            id=loc_id,
            project_id=project_id,
            index=index,
            scene_id=scene_by_title.get(str(card.get("scene_title") or "")),
            name=str(card.get("name") or f"场景{index}"),
            description=str(card.get("description") or ""),
            visual_style=str(card.get("visual_style") or ""),
            time_of_day_default=str(card.get("time_of_day_default") or ""),
            materials=_str_list(card.get("materials")),
            colors=_str_list(card.get("colors")),
            visual_cues=_str_list(card.get("visual_cues")),
            immutable_elements=_str_list(card.get("immutable_elements")),
            lighting_rules=_str_list(card.get("lighting_rules")),
            seed=stable_seed(loc_id),
            source="AUTO",
            status="DRAFT",
        )
        session.add(location)
        locations.append(location)
    session.commit()
    for item in (*characters, *locations):
        session.refresh(item)
    return characters, locations


def enqueue_asset_job(
    session: Session,
    project_id: str,
    owner_id: str,
    job_type: str,
    prompt: str,
    views: list[str],
    width: int = 1024,
    height: int = 576,
) -> GenerationJob:
    index, job_id = next_seq_and_id(session, GenerationJob, project_id, "job")
    job = GenerationJob(
        id=job_id,
        project_id=project_id,
        index=index,
        job_type=job_type,
        priority="HIGH",
        payload={
            "owner_id": owner_id,
            "prompt": prompt,
            "views": views,
            "width": width,
            "height": height,
        },
    )
    session.add(job)
    session.commit()
    session.refresh(job)
    generation_queue.notify()
    return job


def enqueue_character_turnaround(
    session: Session, project_id: str, character: Character, prompt: str
) -> GenerationJob:
    return enqueue_asset_job(
        session,
        project_id,
        character.id,
        "CHARACTER",
        prompt,
        list(TURNAROUND_VIEWS),
        width=768,
        height=1024,
    )


def enqueue_location_refs(
    session: Session, project_id: str, location: Location, prompt: str
) -> GenerationJob:
    return enqueue_asset_job(
        session,
        project_id,
        location.id,
        "LOCATION",
        prompt,
        list(LOCATION_REF_KINDS),
    )


def store_generated_asset(
    session: Session,
    project_id: str,
    owner_id: str,
    asset_type: str,
    view: str,
    image_path: str,
    model: str,
    seed: int | None,
) -> Asset:
    """把生成器产出的临时图搬进项目资产目录并 upsert Asset 行。"""
    subdir = CHARACTER_SUBDIR if asset_type == CHARACTER_ASSET_TYPE else LOCATION_SUBDIR
    suffix = Path(image_path).suffix or ".png"
    rel = f"{subdir}/{owner_id}_{view}{suffix}"
    dest = abs_path(project_id, rel)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(image_path, dest)

    asset_id = f"{owner_id}_{view}"
    asset = session.get(Asset, asset_id)
    if asset is None:
        asset = Asset(id=asset_id, project_id=project_id, type=asset_type, path=rel)
    asset.path = rel
    asset.source = "GENERATED"
    asset.meta = {
        "owner_id": owner_id,
        "view": view,
        "model": model,
        "seed": seed,
        "generated_by": asset_type,
    }
    session.add(asset)
    session.commit()
    session.refresh(asset)
    return asset


def store_uploaded_asset(
    session: Session,
    project_id: str,
    owner_id: str,
    asset_type: str,
    view: str,
    filename: str,
    data: bytes,
) -> Asset:
    """Import 入口：用户上传参考图（规格书第 13.3 节）。"""
    subdir = CHARACTER_SUBDIR if asset_type == CHARACTER_ASSET_TYPE else LOCATION_SUBDIR
    suffix = Path(filename).suffix.lower() or ".png"
    rel = f"{subdir}/{owner_id}_{view}_upload{suffix}"
    dest = abs_path(project_id, rel)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)

    # 与生成图共用同一个 id：一个视图槽位只保留一行，上传即替换自动生成结果，
    # 否则同视图会有两张参考图，shot_asset_blocks 可能超出 MAX_REFERENCE_IMAGES
    asset_id = f"{owner_id}_{view}"
    asset = session.get(Asset, asset_id)
    if asset is None:
        asset = Asset(id=asset_id, project_id=project_id, type=asset_type, path=rel)
    replaced_generated = asset.source == "GENERATED"
    asset.path = rel
    asset.source = "USER"
    asset.meta = {
        "owner_id": owner_id,
        "view": view,
        "original_name": filename,
        "replaced_generated": replaced_generated,
    }
    session.add(asset)
    session.commit()
    session.refresh(asset)
    return asset


def stable_seed(owner_id: str) -> int:
    """确定性种子：同一资产跨镜头/跨轮次复用，是无参考图后端的一致性兜底。"""
    return int(hashlib.md5(owner_id.encode()).hexdigest()[:8], 16) % 100000


def lock_character(session: Session, character: Character) -> Character:
    character.status = "LOCKED"
    character.locked_at = utcnow()
    character.version += 1
    character.prompt_block = character_prompt_block(character)
    character.prompt_block_hash = hashlib.sha256(
        character.prompt_block.encode()
    ).hexdigest()[:16]
    if character.seed is None:
        character.seed = stable_seed(character.id)
    session.add(character)
    session.commit()
    session.refresh(character)
    return character


def lock_location(session: Session, location: Location) -> Location:
    location.status = "LOCKED"
    location.locked_at = utcnow()
    location.version += 1
    location.prompt_block = location_prompt_block(location)
    location.prompt_block_hash = hashlib.sha256(
        location.prompt_block.encode()
    ).hexdigest()[:16]
    if location.seed is None:
        location.seed = stable_seed(location.id)
    session.add(location)
    session.commit()
    session.refresh(location)
    return location


def locked_characters(session: Session, project_id: str) -> list[Character]:
    return list(
        session.exec(
            select(Character)
            .where(
                Character.project_id == project_id,
                Character.status == "LOCKED",
            )
            .order_by(Character.index)
        )
    )


def locked_locations(session: Session, project_id: str) -> list[Location]:
    return list(
        session.exec(
            select(Location)
            .where(
                Location.project_id == project_id,
                Location.status == "LOCKED",
            )
            .order_by(Location.index)
        )
    )


def link_shots_to_assets(session: Session, project_id: str) -> dict[str, list[str]]:
    """规格书第 14 节「锁定后所有 Shot 自动引用」：

    场景资产按 scene_id 绑定；角色按名字在镜头/场景描述中的出现匹配。
    返回 {shot_id: [character_id...]}。
    """
    shots = list(
        session.exec(
            select(Shot).where(Shot.project_id == project_id).order_by(Shot.index)
        )
    )
    characters = locked_characters(session, project_id)
    locations = locked_locations(session, project_id)
    loc_by_scene = {loc.scene_id: loc for loc in locations if loc.scene_id}
    # 镜头没点名角色时的兜底：主要角色默认出镜（否则资产锚点永远不会注入提示词）
    primary_ids = [
        c.id for c in characters if c.role == "PRIMARY"
    ][:MAX_CHARACTERS_PER_SHOT]
    linked: dict[str, list[str]] = {}

    for shot in shots:
        scene = session.get(Scene, shot.scene_id) if shot.scene_id else None
        haystack = " ".join(
            filter(None, [shot.title, shot.description, scene.description if scene else ""])
        )
        matched = [c.id for c in characters if c.name and c.name in haystack][
            :MAX_CHARACTERS_PER_SHOT
        ]
        if not matched:
            matched = list(primary_ids)
        location = loc_by_scene.get(shot.scene_id) if shot.scene_id else None
        if location is None:
            location = next(
                (loc for loc in locations if loc.name and loc.name in haystack), None
            )

        changed = False
        if shot.character_ids != matched:
            shot.character_ids = matched
            changed = True
        new_loc = location.id if location else shot.location_id
        if shot.location_id != new_loc:
            shot.location_id = new_loc
            changed = True
        if changed:
            session.add(shot)
        linked[shot.id] = matched
    session.commit()
    return linked


def character_prompt_block(character: Character) -> str:
    """角色一致性文本锚点。截断时只能砍描述，锚点与不可变特征必须保留。"""
    head = f"角色「{character.name}」"
    if character.age_range or character.gender:
        head += "（" + "，".join(filter(None, [character.gender, character.age_range])) + "）"
    parts = [head]
    if character.appearance:
        parts.append(f"外形 {character.appearance}")
    if character.costume:
        parts.append(f"服装 {character.costume}")
    if character.visual_anchors:
        parts.append("视觉锚点 " + "、".join(character.visual_anchors))
    if character.immutable_traits:
        parts.append("不可变特征 " + "、".join(character.immutable_traits))
    return "；".join(parts)


def location_prompt_block(location: Location) -> str:
    parts = [f"场景「{location.name}」"]
    if location.visual_style:
        parts.append(f"视觉风格 {location.visual_style}")
    if location.time_of_day_default:
        parts.append(f"时段 {location.time_of_day_default}")
    if location.colors:
        parts.append("主色 " + "、".join(location.colors))
    if location.materials:
        parts.append("材质 " + "、".join(location.materials))
    if location.visual_cues:
        parts.append("可视锚点 " + "、".join(location.visual_cues))
    if location.immutable_elements:
        parts.append("固定元素 " + "、".join(location.immutable_elements))
    if location.lighting_rules:
        parts.append("光线 " + "、".join(location.lighting_rules))
    return "；".join(parts)


def shot_asset_blocks(session: Session, project_id: str, shot: Shot) -> dict:
    """给 Prompt Agent / 生成器的资产上下文。

    返回 {"characters": [...], "location": "...", "reference_images": [...], "seed": int|None}
    角色与场景锚点优先复用锁定时固化的 prompt_block，确保逐字一致、不随调用漂移。
    """
    blocks: list[str] = []
    refs: list[tuple[int, str]] = []
    seed: int | None = None
    root = project_path(project_id)

    for cid in (shot.character_ids or [])[:MAX_CHARACTERS_PER_SHOT]:
        character = session.get(Character, cid)
        if character is None or character.status != "LOCKED":
            continue
        blocks.append(character.prompt_block or character_prompt_block(character))
        if seed is None:
            seed = character.seed
        for asset in refs_of(session, project_id, cid):
            refs.append(
                (REF_VIEW_PRIORITY.get(str(asset.meta.get("view")), 9), str(root / asset.path))
            )

    location_block = ""
    if shot.location_id:
        location = session.get(Location, shot.location_id)
        if location is not None and location.status == "LOCKED":
            location_block = location.prompt_block or location_prompt_block(location)
            if seed is None:
                seed = location.seed
            for asset in refs_of(session, project_id, location.id):
                refs.append(
                    (
                        REF_VIEW_PRIORITY.get(str(asset.meta.get("view")), 9),
                        str(root / asset.path),
                    )
                )

    refs.sort(key=lambda item: item[0])
    unique: list[str] = []
    for _, path in refs:
        if path not in unique:
            unique.append(path)
    return {
        "characters": blocks,
        "location": location_block,
        "reference_images": unique[:MAX_REFERENCE_IMAGES],
        "seed": seed,
    }
