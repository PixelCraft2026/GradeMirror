"""One active desktop command identity; separate from the resident result."""
from dataclasses import dataclass
from pathlib import Path
import time


@dataclass
class ActiveOperation:
    kind: str
    request_key: str
    store: Path
    job_id: str | None = None
    attempt_id: str | None = None
    cancel_requested: bool = False
    close_requested_at: float | None = None
    forced_cleanup: bool = False
    prior_address: tuple[str, str] | None = None

    def address(self, reply):
        if reply and reply.get("job_id") and reply.get("attempt_id"):
            ids = reply["job_id"], reply["attempt_id"]
            if ids == self.prior_address:
                return False
            handoff = reply.get("cpu_fine_handoff")
            if handoff is not None:
                prior = handoff.get("parent_job_id"), handoff.get("parent_attempt_id")
                if (self.kind != "fit" or handoff.get("request_key") != self.request_key
                        or not all(prior) or prior == ids
                        or (self.prior_address is not None and
                            (self.prior_address != prior or ids != (self.job_id, self.attempt_id)))
                        or (self.prior_address is None and self.job_id is not None and
                            (self.job_id, self.attempt_id) != prior)):
                    raise ValueError("Active operation address changed")
                self.prior_address = prior
            elif self.job_id is not None and ids != (self.job_id, self.attempt_id):
                raise ValueError("Active operation address changed")
            self.job_id, self.attempt_id = ids
        return True

    def request_close(self):
        if self.close_requested_at is None:
            self.close_requested_at = time.monotonic()
