from pydantic import BaseModel, Field, ConfigDict, field_validator
from typing import Any, Dict

class PulumiConfigModel(BaseModel):
    """Validate Pulumi config — minimal required keys only."""
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    project: str
    zone: str
    stack_name: str        = Field(..., validation_alias="stackName")
    service_account: str   = Field(..., validation_alias="serviceAccount")
    vm_name: str           = Field(..., validation_alias="vmName")
    machine_type: str      = Field(..., validation_alias="machineType")
    os_image: str          = Field(..., validation_alias="osImage")
    boot_size: int         = Field(..., validation_alias="bootSize")
    service: str
    provisioning_model: str = Field(..., validation_alias="provisioningModel")

    @classmethod
    def from_pulumi_config(cls, config_dict: Dict[str, Any]) -> Dict[str, Any]:
        """
        Validate minimal required keys, but return *all* original keys intact.
        """
        # ✅ Step 1 — Validate only the fields defined above
        model = cls.model_validate(config_dict)

        # ✅ Step 2 — Merge validated (typed) fields back into the original dict
        validated_snake = model.model_dump(by_alias=False)

        # ✅ Step 3 — Preserve all keys (validated + extras)
        merged = {**config_dict, **validated_snake}

        return merged


class HFValidation(BaseModel):
    """
    Validate Hugging Face–related config before use.
    """
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    model_repo: str        = Field(...)
    model_repo_type: str   = Field(...)
    model_image: str       = Field(...)
    branch: str            = Field(...)

    @field_validator("model_repo_type", mode="after")
    def validate_type(cls, v: str) -> str:
        allowed = {"model", "dataset"}
        if v not in allowed:
            raise ValueError(
                f"Invalid model_repo_type '{v}'. Must be one of {allowed}"
            )
        return v


    @classmethod
    def from_pulumi_config(cls, config_dict: Dict[str, Any]) -> Dict[str, Any]:
        """
        Validate minimal required keys, but return *all* original keys intact.
        """
        # ✅ Step 1 — Validate only the fields defined above
        model = cls.model_validate(config_dict)

        # ✅ Step 2 — Merge validated (typed) fields back into the original dict
        validated_snake = model.model_dump(by_alias=False)

        # ✅ Step 3 — Preserve all keys (validated + extras)
        merged = {**config_dict, **validated_snake}

        return merged


class DockerValidation(BaseModel):
    """
    Validate Hugging Face–related config before use.
    """
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    docker_images: str = Field(...)


    @classmethod
    def from_pulumi_config(cls, config_dict: Dict[str, Any]) -> Dict[str, Any]:
        """
        Validate minimal required keys, but return *all* original keys intact.
        """
        # ✅ Step 1 — Validate only the fields defined above
        model = cls.model_validate(config_dict)

        # ✅ Step 2 — Merge validated (typed) fields back into the original dict
        validated_snake = model.model_dump(by_alias=False)

        # ✅ Step 3 — Preserve all keys (validated + extras)
        merged = {**config_dict, **validated_snake}

        return merged


class DockerVMValidation(BaseModel):
    """
    Validate Workstation–related config before use.
    """
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    domain: str                 = Field(...)
    url_map: str                = Field(...)
    oauth_client: str           = Field(...)
    oauth_secret: str           = Field(...)
    iap_user: list              = Field(...)

    @classmethod
    def from_pulumi_config(cls, config_dict: Dict[str, Any]) -> Dict[str, Any]:
        """
        Validate minimal required keys, but return *all* original keys intact.
        """
        # ✅ Step 1 — Validate only the fields defined above
        model = cls.model_validate(config_dict)

        # ✅ Step 2 — Merge validated (typed) fields back into the original dict
        validated_snake = model.model_dump(by_alias=False)

        # ✅ Step 3 — Preserve all keys (validated + extras)
        merged = {**config_dict, **validated_snake}

        return merged
