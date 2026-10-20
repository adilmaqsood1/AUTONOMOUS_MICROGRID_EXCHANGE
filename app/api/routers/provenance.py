"""Renewable energy provenance and certificate lifecycle router."""
import uuid
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Principal, SessionDep, current_principal, reader
from app.models.ops import RenewableCertificate
from app.provenance import service as prov_service
from app.schemas.schemas import (
    CertificateIssueRequest, CertificateResponse, CertificateTransfer
)

router = APIRouter(prefix="/renewable-proofs", tags=["Renewable Provenance"])


@router.get("", response_model=List[CertificateResponse], dependencies=[Depends(reader)])
async def list_certificates(microgrid_id: Optional[uuid.UUID] = None,
                            owner_id: Optional[uuid.UUID] = None,
                            status_filter: Optional[str] = None,
                            limit: int = 100,
                            session: AsyncSession = SessionDep):
    q = select(RenewableCertificate)
    if microgrid_id:
        q = q.where(RenewableCertificate.microgrid_id == microgrid_id)
    if owner_id:
        q = q.where(RenewableCertificate.owner_id == owner_id)
    if status_filter:
        q = q.where(RenewableCertificate.status == status_filter)
    return (await session.execute(q.order_by(RenewableCertificate.created_at.desc()).limit(min(limit, 500)))).scalars().all()


@router.get("/{cert_id}", response_model=CertificateResponse, dependencies=[Depends(reader)])
async def get_certificate(cert_id: uuid.UUID, session: AsyncSession = SessionDep):
    c = await session.get(RenewableCertificate, cert_id)
    if not c:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "certificate not found")
    return c


@router.post("/issue", response_model=CertificateResponse)
async def issue_cert(req: CertificateIssueRequest,
                     principal: Principal = Depends(current_principal),
                     session: AsyncSession = SessionDep):
    cert = await prov_service.issue_certificate(session, req.reading_id, actor=principal.actor)
    await session.commit()
    await session.refresh(cert)
    return cert


@router.post("/{cert_id}/transfer", response_model=CertificateResponse)
async def transfer_cert(cert_id: uuid.UUID,
                        req: CertificateTransfer,
                        principal: Principal = Depends(current_principal),
                        session: AsyncSession = SessionDep):
    if not principal.participant_id and not principal.is_privileged:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "must be a participant or operator")
    c = await session.get(RenewableCertificate, cert_id)
    if not c:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "certificate not found")
    from_owner = c.owner_id if principal.is_privileged else principal.participant_id
    cert = await prov_service.transfer_certificate(session, cert_id, from_owner, req.to_owner, actor=principal.actor)
    await session.commit()
    await session.refresh(cert)
    return cert


@router.post("/{cert_id}/retire", response_model=CertificateResponse)
async def retire_cert(cert_id: uuid.UUID,
                      principal: Principal = Depends(current_principal),
                      session: AsyncSession = SessionDep):
    c = await session.get(RenewableCertificate, cert_id)
    if not c:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "certificate not found")
    owner_id = c.owner_id if principal.is_privileged else principal.participant_id
    cert = await prov_service.retire_certificate(session, cert_id, owner_id, actor=principal.actor)
    await session.commit()
    await session.refresh(cert)
    return cert
