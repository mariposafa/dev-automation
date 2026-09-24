import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class DatabaseConfig:
    # repr=False mencegah URI berkredensial ikut tampil saat objek dicetak.
    uri: str = field(repr=False)
    database: str

    @classmethod
    def from_env(cls):
        names = ("MONGODB_URI", "MONGODB_DATABASE")
        values = {name: os.environ.get(name, "").strip() for name in names}
        missing = [name for name, value in values.items() if not value]
        if missing:
            raise RuntimeError("Environment variable wajib diisi: " + ", ".join(missing))
        if not values["MONGODB_URI"].startswith(("mongodb://", "mongodb+srv://")):
            raise RuntimeError("MONGODB_URI harus diawali mongodb:// atau mongodb+srv://.")
        return cls(uri=values["MONGODB_URI"], database=values["MONGODB_DATABASE"])
