"""Solana blockchain tools for SAID-verified AI agents.

Updated for SAID Protocol v2 API (July 2026):
- Corrected API endpoints (/api/verify/, /v1/score/, /api/stats)
- Added: trust score with breakdown, feedback submission, discover agents,
  leaderboard, network stats, slashed agent detection
- Enforcement-aware: tools check slashing status before recommending interactions
"""

import json
import os
import urllib.request
import urllib.error
from pathlib import Path
from typing import Any

from nanobot.agent.tools.base import Tool

# SAID API
SAID_API = os.getenv("SAID_API", "https://api.saidprotocol.com")


def _fetch_json(url: str, timeout: int = 10) -> dict | None:
    """Fetch JSON from a URL, return None on 404."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise
    except Exception:
        return None


def _post_json(url: str, data: dict, timeout: int = 10) -> dict:
    """POST JSON to a URL."""
    payload = json.dumps(data).encode()
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


class GetBalanceTool(Tool):
    """Get SOL balance for a wallet address."""

    @property
    def name(self) -> str:
        return "get_sol_balance"

    @property
    def description(self) -> str:
        return "Get SOL balance for a Solana wallet address. Returns balance in SOL."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "address": {
                    "type": "string",
                    "description": "Solana wallet address (base58)",
                }
            },
            "required": ["address"],
        }

    async def execute(self, address: str) -> str:
        try:
            rpc_url = os.getenv(
                "SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com"
            )
            payload = json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "getBalance",
                    "params": [address],
                }
            ).encode()

            req = urllib.request.Request(
                rpc_url,
                data=payload,
                headers={"Content-Type": "application/json"},
            )

            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())
                if "result" in data:
                    lamports = data["result"]["value"]
                    sol = lamports / 1_000_000_000
                    return json.dumps(
                        {
                            "address": address,
                            "balance_sol": sol,
                            "balance_lamports": lamports,
                        }
                    )
                else:
                    return json.dumps(
                        {"error": data.get("error", "Unknown error")}
                    )
        except Exception as e:
            return json.dumps({"error": str(e)})


class VerifyAgentTool(Tool):
    """Verify another agent's SAID identity. Enforcement-aware."""

    @property
    def name(self) -> str:
        return "verify_said_agent"

    @property
    def description(self) -> str:
        return (
            "Verify if a wallet is a registered SAID agent. "
            "Checks registration, verification status, trust score, and slashing status. "
            "Use BEFORE transacting with unknown agents — slashed agents should be avoided."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "wallet": {
                    "type": "string",
                    "description": "Solana wallet address to verify",
                }
            },
            "required": ["wallet"],
        }

    async def execute(self, wallet: str) -> str:
        agent = _fetch_json(f"{SAID_API}/api/verify/{wallet}")
        if not agent:
            return json.dumps(
                {
                    "verified": False,
                    "error": "Agent not found in SAID registry",
                    "wallet": wallet,
                }
            )

        # Check for slashing (enforcement)
        slashed = agent.get("slashed", False)
        score = None
        if agent.get("trustScore"):
            score = agent["trustScore"].get("score")

        result = {
            "verified": agent.get("verified", False),
            "registered": agent.get("registered", False),
            "name": agent.get("identity", {}).get("name"),
            "wallet": agent.get("wallet", wallet),
            "trust_score": score,
            "trust_tier": agent.get("trustScore", {}).get("tier") if agent.get("trustScore") else None,
            "slashed": slashed,
            "reputation_score": agent.get("reputation", {}).get("score"),
            "feedback_count": agent.get("reputation", {}).get("feedbackCount", 0),
            "description": agent.get("identity", {}).get("description"),
            "profile": f"https://www.saidprotocol.com/agent.html?wallet={wallet}",
        }

        # Enforcement warning
        if slashed:
            result["WARNING"] = "This agent has been SLASHED by SAID Protocol. Economic enforcement applied. Avoid transacting."
        elif not agent.get("verified"):
            result["note"] = "Agent is registered but not verified. Limited trust."

        return json.dumps(result)


class GetTrustScoreTool(Tool):
    """Get detailed trust score breakdown for an agent."""

    @property
    def name(self) -> str:
        return "get_trust_score"

    @property
    def description(self) -> str:
        return (
            "Get the detailed trust score for a SAID agent including "
            "breakdown by category (identity, activity, economic, ecosystem, longevity)."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "wallet": {
                    "type": "string",
                    "description": "Solana wallet address",
                }
            },
            "required": ["wallet"],
        }

    async def execute(self, wallet: str) -> str:
        # Try the score breakdown endpoint first
        score = _fetch_json(f"{SAID_API}/v1/score/{wallet}")
        if score:
            return json.dumps(score)
        # Fallback to verify endpoint
        agent = _fetch_json(f"{SAID_API}/api/verify/{wallet}")
        if agent and agent.get("trustScore"):
            return json.dumps(agent["trustScore"])
        return json.dumps({"error": "Trust score not available for this wallet"})


class DiscoverAgentsTool(Tool):
    """Discover SAID-verified agents."""

    @property
    def name(self) -> str:
        return "discover_said_agents"

    @property
    def description(self) -> str:
        return (
            "Browse the SAID agent directory. Returns top agents by trust score. "
            "Use to find verified service providers for tasks."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Max results (default 10, max 50)",
                }
            },
        }

    async def execute(self, limit: int = 10) -> str:
        data = _fetch_json(f"{SAID_API}/api/agents?limit={min(limit, 50)}")
        if data:
            agents = data if isinstance(data, list) else data.get("agents", [])
            return json.dumps(
                {
                    "count": len(agents),
                    "agents": agents[:limit],
                }
            )
        return json.dumps({"error": "Failed to fetch agent directory"})


class GetLeaderboardTool(Tool):
    """Get SAID trust leaderboard."""

    @property
    def name(self) -> str:
        return "get_said_leaderboard"

    @property
    def description(self) -> str:
        return "Get the SAID Protocol trust leaderboard — top agents by reputation and trust score."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Max results (default 10)",
                }
            },
        }

    async def execute(self, limit: int = 10) -> str:
        data = _fetch_json(f"{SAID_API}/api/leaderboard?limit={limit}")
        if data:
            return json.dumps(data)
        return json.dumps({"error": "Failed to fetch leaderboard"})


class GetNetworkStatsTool(Tool):
    """Get SAID Protocol network statistics."""

    @property
    def name(self) -> str:
        return "get_said_stats"

    @property
    def description(self) -> str:
        return "Get SAID Protocol network statistics — total agents, verified count, trust distribution."

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def execute(self) -> str:
        data = _fetch_json(f"{SAID_API}/api/stats")
        if data:
            return json.dumps(data)
        return json.dumps({"error": "Failed to fetch stats"})


class SubmitFeedbackTool(Tool):
    """Submit feedback for a SAID agent."""

    @property
    def name(self) -> str:
        return "submit_said_feedback"

    @property
    def description(self) -> str:
        return (
            "Submit feedback/review for a SAID agent after an interaction. "
            "Rate 1-5 stars with optional comment. Helps build trust reputation."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "wallet": {
                    "type": "string",
                    "description": "Agent wallet address to review",
                },
                "rating": {
                    "type": "integer",
                    "description": "Rating 1-5 (5=excellent)",
                },
                "comment": {
                    "type": "string",
                    "description": "Optional feedback comment",
                },
                "reviewer": {
                    "type": "string",
                    "description": "Reviewer wallet address",
                },
            },
            "required": ["wallet", "rating"],
        }

    async def execute(
        self,
        wallet: str,
        rating: int,
        comment: str = "",
        reviewer: str = "",
    ) -> str:
        try:
            result = _post_json(
                f"{SAID_API}/api/feedback",
                {
                    "wallet": wallet,
                    "rating": max(1, min(5, rating)),
                    "comment": comment,
                    "reviewer": reviewer,
                },
            )
            return json.dumps({"success": True, "result": result})
        except Exception as e:
            return json.dumps({"success": False, "error": str(e)})


class RegisterAgentTool(Tool):
    """Register as a SAID agent (pending status)."""

    @property
    def name(self) -> str:
        return "register_said_agent"

    @property
    def description(self) -> str:
        return "Register a new agent on SAID Protocol with pending status (free, instant)."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "wallet": {
                    "type": "string",
                    "description": "Solana wallet address",
                },
                "name": {
                    "type": "string",
                    "description": "Agent name",
                },
                "description": {
                    "type": "string",
                    "description": "Agent description",
                },
            },
            "required": ["wallet", "name"],
        }

    async def execute(
        self, wallet: str, name: str, description: str = ""
    ) -> str:
        try:
            result = _post_json(
                f"{SAID_API}/api/register/pending",
                {
                    "wallet": wallet,
                    "name": name,
                    "description": description or f"{name} - AI Agent",
                },
            )
            return json.dumps(
                {
                    "success": True,
                    "wallet": result.get("wallet"),
                    "pda": result.get("pda"),
                    "profile": result.get("profile"),
                    "status": "PENDING",
                }
            )
        except Exception as e:
            return json.dumps({"success": False, "error": str(e)})


class GetMyIdentityTool(Tool):
    """Get the agent's own SAID identity from local config."""

    def __init__(self, workspace: Path | None = None):
        self.workspace = workspace or Path.cwd()

    @property
    def name(self) -> str:
        return "get_my_said_identity"

    @property
    def description(self) -> str:
        return "Get your own SAID identity information from local said.json file."

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def execute(self) -> str:
        paths = [
            self.workspace / "said.json",
            Path.home() / ".nanobot" / "said.json",
            Path.home() / ".config" / "said" / "identity.json",
        ]

        for path in paths:
            if path.exists():
                try:
                    with open(path) as f:
                        return f.read()
                except Exception:
                    continue

        return json.dumps(
            {
                "error": "SAID identity not found. Register first with register_said_agent."
            }
        )


# Export all tools
SOLANA_TOOLS = [
    GetBalanceTool,
    VerifyAgentTool,
    GetTrustScoreTool,
    DiscoverAgentsTool,
    GetLeaderboardTool,
    GetNetworkStatsTool,
    SubmitFeedbackTool,
    RegisterAgentTool,
    GetMyIdentityTool,
]
