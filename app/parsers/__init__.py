"""解析器包：EML 与附件统一入口."""
from .attachment_parser import parse_attachment
from .email_parser import parse_eml

__all__ = ["parse_attachment", "parse_eml"]
