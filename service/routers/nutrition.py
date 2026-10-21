import csv
from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel, Field

from service.routers.auth import RequireAuth

router = APIRouter(tags=["Nutrition"], dependencies=[RequireAuth])

INGREDIENTS_CSV = Path(__file__).resolve().parents[2] / "nutrition" / "ingredients.csv"


class IngredientResponse(BaseModel):
    """Ingredient with macros per 100 g."""

    id: str = Field(..., description="Stable ingredient id (slug).")
    name: str = Field(..., description="Ingredient name.")
    food_group: str = Field(..., description="Food group, e.g. meso, žitarice.")
    meal_role: str = Field(..., description="Meal slot it fills, e.g. Proteini.")
    state: str = Field(..., description="raw, dry or ready: how it is weighed.")
    kcal: float = Field(..., description="kcal per 100 g.")
    protein: float = Field(..., description="Protein (g) per 100 g.")
    carbs: float = Field(..., description="Carbohydrates (g) per 100 g.")
    fat: float = Field(..., description="Fat (g) per 100 g.")
    grams_per_piece: float | None = Field(..., description="Weight of one piece.")
    ml_to_g: float | None = Field(..., description="Grams per ml for liquids.")
    preference: str = Field(..., description="Default preference: green/yellow/red.")
    source: str = Field(..., description="Where the values come from.")
    note: str = Field(..., description="Changes and things to check.")


class ListIngredientsResponse(BaseModel):
    """List ingredients response schema."""

    ingredients: list[IngredientResponse] = Field(..., description="All ingredients.")


def _num(value: str) -> float | None:
    return float(value) if value.strip() else None


@router.get("/ingredients/", summary="List ingredients with macros")
async def list_ingredients() -> ListIngredientsResponse:
    """
    List all ingredients from nutrition/ingredients.csv.

    The file is read on every request, so edits show up without a restart.
    """
    with open(INGREDIENTS_CSV, encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))

    ingredients = [
        IngredientResponse(
            id=r["id"],
            name=r["name"],
            food_group=r["food_group"],
            meal_role=r["meal_role"],
            state=r["state"],
            kcal=float(r["kcal"]),
            protein=float(r["protein"]),
            carbs=float(r["carbs"]),
            fat=float(r["fat"]),
            grams_per_piece=_num(r["grams_per_piece"]),
            ml_to_g=_num(r["ml_to_g"]),
            preference=r["preference"] or "green",
            source=r["source"],
            note=r["note"],
        )
        for r in rows
    ]
    return ListIngredientsResponse(ingredients=ingredients)
