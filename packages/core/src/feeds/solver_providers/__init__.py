"""Explicitly registered solver adapters; configuration never imports arbitrary code."""

from devfeed_core.config import SolverService
from devfeed_core.feeds.solver_providers.flaresolverr import FlareSolverr
from devfeed_core.feeds.solver_types import Solver


def create_solver(service: SolverService) -> Solver:
    providers = {"flaresolverr": FlareSolverr}
    return providers[service.provider](service.url)
