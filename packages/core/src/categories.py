import uuid

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from devfeed_core.models import Category

MAX_CATEGORY_DEPTH = 16


class CategoryNotFound(ValueError):
    pass


class InvalidCategoryTree(ValueError):
    pass


def lock_category_tree(session: Session) -> None:
    # Taxonomy edits are rare. Serialize writers before reading the tree so two
    # simultaneous reparentings cannot each validate against an outdated tree.
    # Ordinary category/feed reads can continue while this lock is held.
    session.execute(text("LOCK TABLE categories IN SHARE ROW EXCLUSIVE MODE"))


def validate_parent(session: Session, category_id: uuid.UUID, parent_id: uuid.UUID | None) -> None:
    """Caller holds the tree write lock for validation and commit."""
    parents = {
        identifier: parent
        for identifier, parent in session.execute(select(Category.id, Category.parent_id))
    }
    if parent_id is not None and parent_id != category_id and parent_id not in parents:
        raise CategoryNotFound("Parent category not found")
    parents[category_id] = parent_id
    for identifier in parents:
        visited: set[uuid.UUID] = set()
        current: uuid.UUID | None = identifier
        while current is not None:
            if current in visited:
                raise InvalidCategoryTree("A category cannot be its own ancestor")
            visited.add(current)
            if len(visited) > MAX_CATEGORY_DEPTH:
                raise InvalidCategoryTree(
                    f"Category nesting is limited to {MAX_CATEGORY_DEPTH} levels"
                )
            current = parents[current]


def descendant_ids(slug: str):
    descendants = (
        select(Category.id).where(Category.slug == slug).cte("category_descendants", recursive=True)
    )
    # UNION also terminates if invalid data was written outside the taxonomy API.
    descendants = descendants.union(
        select(Category.id).where(Category.parent_id == descendants.c.id)
    )
    return select(descendants.c.id)
