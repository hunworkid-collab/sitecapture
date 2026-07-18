class Stage1Error(Exception):
    pass


class ChromeNotFoundError(Stage1Error):
    pass


class ChromeLaunchError(Stage1Error):
    pass


class CdpError(Stage1Error):
    def __init__(
        self,
        message: str,
        *,
        method: str | None = None,
        data: object = None,
    ) -> None:
        super().__init__(message)
        self.method = method
        self.data = data


class CdpTimeoutError(CdpError):
    pass


class BrowserDisconnectedError(CdpError):
    pass


class BrowserRecoveryError(CdpError):
    pass


class SearchBoxNotFoundError(Stage1Error):
    pass


class CaptureAreaNotFoundError(Stage1Error):
    pass


class UserActionRequiredError(Stage1Error):
    pass


class RunCancelled(Stage1Error):
    pass
