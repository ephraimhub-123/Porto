import logging
import os
import re
from html import escape
from pathlib import Path

from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger("portofilio")

# --- Settings -------------------------------------------------------------
# Edit these to make the portfolio yours. They feed both the page and the API.
NAME = os.getenv("PORTFOLIO_NAME", "Your Name")
ROLE = "Developer & creative problem solver"
EMAIL = os.getenv("PORTFOLIO_EMAIL", "hello@example.com")
SITE_URL = os.getenv("PORTFOLIO_SITE_URL", "http://127.0.0.1:8000")
BASE_DIR = Path(__file__).resolve().parent
# The profile picture is a fixed file shipped with the site. To change it,
# replace assets/me.jpg — there is no upload form, and nothing can be uploaded
# to the site at runtime.
PHOTO_PATH = BASE_DIR / "assets" / "me.jpg"
PHOTO_URL = "/profile.jpg"

# Opt in explicitly: re-render the page on every request (handy while editing),
# instead of paying that cost on every request in production by default.
DEVELOPMENT_MODE = os.getenv("PORTFOLIO_DEV", "0").lower() in {"1", "true", "yes"}

app = FastAPI(
    title="Personal Portfolio",
    description="A personal portfolio website and projects API.",
    version="1.1.0",
)

# Compresses responses over 500 bytes (the HTML page is ~13 KB).
app.add_middleware(GZipMiddleware, minimum_size=500)


# --- Data ----------------------------------------------------------------
class Project(BaseModel):
    """A portfolio project. Extra keys are rejected so typos fail loudly."""

    number: str = Field(pattern=r"^\d{2}$", description="Two-digit display number, e.g. '01'.")
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(min_length=1, max_length=300)
    stack: list[str] = Field(default_factory=list, max_length=10)
    url: str = Field(min_length=1)

    @field_validator("url")
    @classmethod
    def _allow_http_or_relative(cls, value: str) -> str:
        # Block javascript:/data: URLs, which the page renders straight into href.
        if value.startswith("/"):
            return value
        if value.startswith(("https://", "http://", "mailto:")):
            return value
        raise ValueError("url must start with https://, http://, mailto: or /")


PROJECTS = [
    Project(
        number="01",
        name="Project One",
        description="A thoughtfully designed web experience built around a real user need.",
        stack=["Python", "FastAPI", "HTML & CSS"],
        url="https://github.com/",
    ),
    Project(
        number="02",
        name="Project Two",
        description="A useful, reliable application that makes a complicated task feel simple.",
        stack=["JavaScript", "API", "Responsive design"],
        url="https://github.com/",
    ),
    Project(
        number="03",
        name="Project Three",
        description="An experiment in clean interfaces, thoughtful details, and fast performance.",
        stack=["Python", "UI/UX", "Web"],
        url="https://github.com/",
    ),
]

PROJECTS_BY_NUMBER = {project.number: project for project in PROJECTS}

TRUSTED_CONTACT = "mailto:" + EMAIL
TAGS = sorted({tag for project in PROJECTS for tag in project.stack})
LARGE_ICON = "/icons/icon-512.png"


class ContactRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    email: str = Field(min_length=3, max_length=200, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    message: str = Field(min_length=10, max_length=5000)


class ContactResponse(BaseModel):
    status: str = "received"
    message: str


class Profile(BaseModel):
    name: str
    role: str
    email: str
    photo_url: str | None = None
    has_photo: bool


# --- Profile photo ----------------------------------------------------------
def find_photo() -> Path | None:
    """Return the fixed profile picture file, if it is present."""
    return PHOTO_PATH if PHOTO_PATH.is_file() else None


def photo_url() -> str | None:
    """URL of the fixed picture; the avatar falls back to initials without it."""
    return PHOTO_URL if find_photo() else None

# The page skeleton lives in template/index.html — edit it there, not here.
TEMPLATE_PATH = Path(__file__).with_name("template") / "index.html"
PAGE = TEMPLATE_PATH.read_text(encoding="utf-8")

# @@name@@ style markers: safe to search for from Python, impossible to confuse
# with the { } used by the JavaScript on the page.
PLACEHOLDER = re.compile(r"@@(\w+)@@")

# One-entry render cache, keyed by the template and photo modification times.
_pre_rendered_page: dict[tuple[int, int], str] = {}


def render_page() -> str:
    """Fill every @@placeholder@@ in PAGE in a single regex pass.

    Chained str.replace calls are fragile: a marker added to the template but
    not handled by the renderer ships as literal text to the page. One pass over
    one pattern makes a missing key a loud error.
    """

    def card(project: Project) -> str:
        tags = "".join(f'<span class="tag">{escape(tag)}</span>' for tag in project.stack)
        return (
            f'<a class="project" href="{escape(project.url, quote=True)}" target="_blank" '
            'rel="noopener noreferrer">'
            f'<span class="project-number">{escape(project.number)}</span>'
            f"<div><h3>{escape(project.name)}</h3><p>{escape(project.description)}</p>"
            f'<div class="tags">{tags}</div></div>'
            '<span class="project-arrow" aria-hidden="true">↗</span></a>'
        )

    initials = "".join(word[0] for word in NAME.split()[:2]).upper() or "ME"
    photo = photo_url()
    # Avatars render the picture server-side, so it shows on first paint with no
    # JavaScript at all, and no upload step exists.
    if photo:
        avatar_inner = f'<img src="{escape(photo, quote=True)}" alt="Profile photo">'
    else:
        avatar_inner = escape(initials)
    values = {
        "photo": escape(photo, quote=True) if photo else "",
        "has_photo": "true" if photo else "false",
        "avatar": avatar_inner,
        "remove_hidden": "" if photo else " hidden",
        "name": escape(NAME),
        "role": escape(ROLE),
        "email": escape(EMAIL),
        "email_href": escape(TRUSTED_CONTACT, quote=True),
        "site_url": escape(SITE_URL.rstrip("/"), quote=True),
        "icon": escape(LARGE_ICON, quote=True),
        "initials": escape(initials),
        "skills": "".join(f'<span class="skill">{escape(tag)}</span>' for tag in TAGS),
        "project_cards": "".join(card(project) for project in PROJECTS),
    }

    def substitute(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in values:  # guard against a typo in PAGE
            raise ValueError(f"Template marker @@{key}@@ has no value")
        return values[key]

    return PLACEHOLDER.sub(substitute, PAGE)


# --- Pages -----------------------------------------------------------------
@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def home() -> HTMLResponse:
    # Cache the page body, keyed on the template AND the picture, so replacing
    # either file takes effect on the next request.
    cache_tag = page_cache_key()
    if DEVELOPMENT_MODE:
        return HTMLResponse(render_page(), headers={"Cache-Control": "no-store"})
    rendered = _pre_rendered_page.get(cache_tag)
    if rendered is None:
        rendered = render_page()
        _pre_rendered_page.clear()
        _pre_rendered_page[cache_tag] = rendered
    return HTMLResponse(rendered, headers={"Cache-Control": "no-cache"})


# --- API -------------------------------------------------------------------
@app.get("/api", tags=["portfolio"], summary="API index")
async def api_index() -> dict[str, object]:
    """Self-describing entry point, so clients don't have to guess at routes."""
    return {
        "name": NAME,
        "role": ROLE,
        "email": EMAIL,
        "projects": len(PROJECTS),
        "links": {
            "home": "/",
            "projects": "/api/projects",
            "docs": "/docs",
            "openapi": "/openapi.json",
            "health": "/health",
        },
    }


@app.get("/api/projects", tags=["portfolio"], summary="List all projects")
async def list_projects() -> list[Project]:
    return PROJECTS


@app.get(
    "/api/projects/{number}",
    tags=["portfolio"],
    summary="Get one project by number",
    responses={404: {"description": "No project with that number"}},
)
async def get_project(number: str) -> Project:
    # "1" and "01" both resolve, so links don't have to guess the padding.
    project = PROJECTS_BY_NUMBER.get(number.zfill(2))
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No project numbered '{number}'.",
        )
    return project


@app.post(
    "/api/contact",
    tags=["portfolio"],
    status_code=status.HTTP_202_ACCEPTED,
    summary="Send a message",
)
async def send_message(payload: ContactRequest) -> ContactResponse:
    """Accept a contact message and log it.

    No mail provider is wired up yet, so nothing is delivered. Replace the
    logger call with your SMTP / Resend / SendGrid send to make it real.
    """
    logger.info(
        "Contact message from %s <%s>: %s",
        payload.name,
        payload.email,
        payload.message.replace("\n", " "),
    )
    return ContactResponse(
        message=f"Thanks {payload.name}, your message was received — I'll reply to {payload.email} soon."
    )


@app.get("/api/profile", tags=["portfolio"], summary="Profile info")
async def get_profile() -> Profile:
    return Profile(
        name=NAME,
        role=ROLE,
        email=EMAIL,
        photo_url=photo_url(),
        has_photo=find_photo() is not None,
    )


@app.get(PHOTO_URL, include_in_schema=False)
async def profile_photo() -> Response:
    """Serve the fixed profile picture.

    No upload route exists: the picture is a file shipped with the site, so
    nothing running here can change or add an image.
    """
    photo = find_photo()
    if photo is None:
        raise HTTPException(404, "Not found.")
    return Response(
        content=photo.read_bytes(),
        media_type="image/jpeg",
        headers={
            "Cache-Control": "public, max-age=31536000, immutable",
            "X-Content-Type-Options": "nosniff",
        },
    )


@app.get("/health", tags=["system"])
async def health_check() -> dict[str, str]:
    return {"status": "ok"}


# --- Static assets ---------------------------------------------------------
# One SVG mark served for every icon path a browser asks for, so the browser
# never 404s on /favicon.ico.
ICON_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" role="img" aria-label="{label}">'
    '<rect width="64" height="64" rx="14" fill="#10110f"/>'
    '<circle cx="47" cy="17" r="5" fill="#c7f36b"/>'
    '<text x="12" y="44" font-family="Georgia, serif" font-size="26" font-weight="700" '
    'fill="#f1f0e9">{initials}</text></svg>'
)


@app.get("/favicon.ico", include_in_schema=False)
@app.get("/icon.svg", include_in_schema=False)
@app.get("/apple-touch-icon.png", include_in_schema=False)
@app.get(LARGE_ICON, include_in_schema=False)
async def icon() -> Response:
    # The fixed picture doubles as the site icon.
    photo = find_photo()
    if photo is not None:
        return Response(
            content=photo.read_bytes(),
            media_type="image/jpeg",
            headers={"Cache-Control": "public, max-age=300"},
        )
    initials = "".join(word[0] for word in NAME.split()[:2]).upper() or "ME"
    svg = ICON_SVG.replace("{initials}", escape(initials)).replace(
        "{label}", escape(f"{NAME} logo", quote=True)
    )
    return Response(content=svg, media_type="image/svg+xml")


@app.get("/robots.txt", include_in_schema=False)
async def robots() -> Response:
    site = SITE_URL.rstrip("/")
    return Response(
        content=f"User-agent: *\nAllow: /\nDisallow: /api/\nSitemap: {site}/sitemap.xml\n",
        media_type="text/plain",
    )


@app.get("/sitemap.xml", include_in_schema=False)
async def sitemap() -> Response:
    site = escape(SITE_URL.rstrip("/"), quote=True)
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        f"<url><loc>{site}/</loc><changefreq>monthly</changefreq><priority>1.0</priority></url>"
        "</urlset>"
    )
    return Response(content=xml, media_type="application/xml")


def page_cache_key() -> tuple[int, int]:
    """Template plus picture mtime: replacing either file invalidates the page."""
    photo = find_photo()
    return (TEMPLATE_PATH.stat().st_mtime_ns, photo.stat().st_mtime_ns if photo else 0)


if not DEVELOPMENT_MODE:
    _pre_rendered_page[page_cache_key()] = render_page()
