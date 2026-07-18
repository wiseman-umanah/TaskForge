"""In-memory agent registry, backed by HCS for durability.

The registry is the coordinator's source of truth for which agents are
currently registered.  On startup it can be rebuilt by replaying
``AgentRegistration`` messages from the HCS topic.

Usage::

    from taskforge.coordinator.registry import AgentRegistry
    reg = AgentRegistry(topic_id="0.0.5678")
    reg.add(registration)
    entry = reg.get("alpha-bot")
    all_agents = reg.list()
"""
from __future__ import annotations

from threading import Lock

from taskforge.ledger.hcs_client import submit_message
from taskforge.models import AgentRegistration, to_json


class AgentRegistry:
    """Thread-safe in-memory agent registry.

    Every successful :meth:`add` call also writes the
    :class:`~taskforge.models.AgentRegistration` to HCS so the record
    survives coordinator restarts (replay on boot via
    :func:`~taskforge.ledger.hcs_client.poll_topic`).

    Attributes:
        topic_id: HCS topic where registration events are logged.
    """

    def __init__(self, topic_id: str) -> None:
        """Create an :class:`AgentRegistry`.

        Args:
            topic_id: HCS topic ID string, e.g. ``"0.0.5678"``.
        """
        self.topic_id = topic_id
        self._agents: dict[str, AgentRegistration] = {}
        self._wins: dict[str, int] = {}
        self._lock = Lock()

    # ── Write ─────────────────────────────────────────────────────────────────

    def add(self, reg: AgentRegistration) -> str:
        """Register an agent and persist the event to HCS.

        If an agent with the same ``agent_id`` is already registered, the
        existing record is overwritten (re-registration after deregister).

        Args:
            reg: Fully populated :class:`~taskforge.models.AgentRegistration`.

        Returns:
            HCS transaction ID of the registration message.
        """
        hcs_tx = submit_message(self.topic_id, to_json(reg))
        with self._lock:
            self._agents[reg.agent_id] = reg
        return hcs_tx

    def remove(self, agent_id: str) -> bool:
        """Deregister an agent by ID.

        Args:
            agent_id: Agent to remove.

        Returns:
            ``True`` if the agent was found and removed, ``False`` if not found.
        """
        with self._lock:
            return self._agents.pop(agent_id, None) is not None

    def load_from_hcs(self, messages: list[dict]) -> None:
        """Replay HCS messages to rebuild registry state on startup.

        Silently skips messages that are not ``AgentRegistration`` type.

        Args:
            messages: List of decoded HCS message dicts as returned by
                :func:`~taskforge.ledger.hcs_client.poll_topic`.
        """
        with self._lock:
            for msg in messages:
                if msg.get("_type") != "AgentRegistration":
                    continue
                try:
                    reg = AgentRegistration(
                        agent_id=msg["agent_id"],
                        account_id=msg["account_id"],
                        claim_url=msg["claim_url"],
                        entry_fee_tx=msg.get("entry_fee_tx", ""),
                        registered_ts=msg.get("registered_ts", 0.0),
                        capabilities=msg.get("capabilities", ["invoice_extraction"]),
                    )
                    self._agents[reg.agent_id] = reg
                except KeyError:
                    continue

    # ── Read ──────────────────────────────────────────────────────────────────

    def get(self, agent_id: str) -> AgentRegistration | None:
        """Look up a single agent by ID.

        Args:
            agent_id: Agent identifier.

        Returns:
            :class:`~taskforge.models.AgentRegistration` or ``None`` if not found.
        """
        with self._lock:
            return self._agents.get(agent_id)

    def get_by_account(self, account_id: str) -> AgentRegistration | None:
        """Look up a registered agent by Hedera account ID.

        Prevents the same Hedera wallet from registering under multiple
        agent IDs (Sybil-resistance).

        Args:
            account_id: Hedera account ID string, e.g. ``"0.0.9999"``.

        Returns:
            :class:`~taskforge.models.AgentRegistration` if the account is
            already registered, ``None`` otherwise.
        """
        with self._lock:
            for reg in self._agents.values():
                if reg.account_id == account_id:
                    return reg
            return None

    def list(self) -> list[AgentRegistration]:
        """Return all currently registered agents.

        Returns:
            List of :class:`~taskforge.models.AgentRegistration` objects,
            sorted by ``registered_ts`` ascending.
        """
        with self._lock:
            return sorted(self._agents.values(), key=lambda r: r.registered_ts)

    def __len__(self) -> int:
        """Return the number of registered agents."""
        with self._lock:
            return len(self._agents)

    def wins(self) -> dict[str, int]:
        """Return a win-count dict keyed by agent_id (all initialised to 0).

        The coordinator's scheduler updates this via :meth:`record_win`.

        Returns:
            Dict of ``{agent_id: win_count}``.
        """
        with self._lock:
            return {aid: self._wins.get(aid, 0) for aid in self._agents}

    def record_win(self, agent_id: str) -> None:
        """Increment win count for an agent.

        Args:
            agent_id: Winner agent.
        """
        with self._lock:
            self._wins[agent_id] = self._wins.get(agent_id, 0) + 1
