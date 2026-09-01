from fastapi import APIRouter

from . import jobs, projects, renders, scenes, shots, storyboards, takes, timeline

api_router = APIRouter()
api_router.include_router(projects.router)
api_router.include_router(scenes.router)
api_router.include_router(shots.router)
api_router.include_router(storyboards.router)
api_router.include_router(takes.router)
api_router.include_router(timeline.router)
api_router.include_router(renders.router)
api_router.include_router(jobs.router)
