"""Explicit admitted primary OLS producer/Save/reuse, with opaque credentials.

One provider-free owned case only. No launcher, restart, cleanup, private login
or cloud-storage substitution. Unknown writes stop and preserve their intent.
"""
from __future__ import annotations

import argparse
import base64
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
from uuid import uuid4

from .opaque_auth import TokenProvider, admit_primary, caller


def run(args, token_provider: TokenProvider | None = None) -> None:
    if not all(p.is_absolute() for p in (args.native_root, args.fixture, args.evidence)):
        raise ValueError('Use explicit absolute owned fixture/evidence paths')
    if args.evidence.exists():
        raise FileExistsError('Inspect the retained attempt; never resubmit it')
    readiness = admit_primary(args.root_readiness, args.root_readiness_sha256,
        native_url=args.native_url, platform_url=args.platform_url,
        native_root=args.native_root, expected_owner=args.expected_owner)
    token, owner = caller(args.expected_owner, token_provider)
    # No SDK/provider work during source preparation or missing-token controls.
    import httpx
    from epoch_sdk.models_api import EpochModels
    from stratifyx import StratifyX
    from .http_owned import execute, table_answers, write
    from .native_artifact import assert_fixture_state, decode_owned_ols
    from .oracle import expected
    from .platform_owned import LostSaveResponse, effects, loopback
    from .source import fixture_source

    native_url, platform_url = loopback(args.native_url), loopback(args.platform_url)
    args.evidence.mkdir(mode=0o700)
    write(args.evidence/'case-intent.json', {'owner':owner, 'root_readiness':str(args.root_readiness),
        'root_readiness_sha256':readiness.sha256, 'native_source':readiness.native_source,
        'native_url':native_url, 'platform_url':platform_url,
        'fixture_sha256':hashlib.sha256(args.fixture.read_bytes()).hexdigest(),
        'expected_producer_fits':1, 'expected_continuation_fits':0,'expected_saved_consumer_fits':0,
        'Save_numerical_executions':0,'scope':'one actual primary server-held owned case; no S3/cleanup/browser claim'})
    opts={'max_retries':0}
    oracle=expected(args.fixture)
    with httpx.Client(timeout=150,trust_env=False) as nh, httpx.Client(timeout=90,trust_env=False) as ph:
        health=nh.get(native_url+'/health');health.raise_for_status()
        if health.json().get('git_sha')!=readiness.native_source:
            raise AssertionError('Actual served Native source differs from root readiness')
        native=StratifyX(base_url=native_url,token=token,httpx_client=nh,follow_redirects=False)
        models=EpochModels(base_url=platform_url,token=token,httpx_client=ph,follow_redirects=False)

        def catalogue():
            result=[];cursor=None
            for _ in range(10):
                page=models.list_models(**({'cursor':cursor} if cursor else {}),request_options=opts)
                result.extend((x.model_ref,x.digest) for x in page.items)
                if not page.next_cursor:return sorted(result)
                cursor=page.next_cursor
            raise ValueError('Owned catalogue exceeds the bounded proof; do not infer global absence')

        initial=catalogue()
        source=fixture_source(args.fixture,stage='fit')
        definition,producer=execute(native,source,'Owned primary exact OLS producer',args.evidence/'fit',args.dispatch)
        table_answers(native,producer,args.evidence/'fit',oracle['holdout'])
        reply=nh.get(native_url+f'/api/v1/studies/executions/{producer}/model-fit-output',
                     headers={'Authorization':'Bearer '+token});reply.raise_for_status()
        envelope=reply.json();descriptor_bytes=base64.b64decode(envelope['descriptor_base64'],validate=True)
        if hashlib.sha256(descriptor_bytes).hexdigest()!=envelope['descriptor_sha256']:
            raise AssertionError('Actual producer descriptor bytes changed')
        descriptor=json.loads(descriptor_bytes)
        if (descriptor['owner_user_id']!=owner or descriptor['execution_id']!=producer
                or descriptor['producer']['study']['source']!=source
                or descriptor['storage_location']!='server' or len(descriptor['fits'])!=1):
            raise AssertionError('New owned server producer/owner/source differs; never substitute storage mode')
        fit=descriptor['fits'][0];digest=fit['artifact']['binding']['digest']
        (args.evidence/'producer-model-fits.json').write_bytes(descriptor_bytes)
        if catalogue()!=initial:
            raise AssertionError('Fitting alone changed the saved catalogue')
        export=nh.get(native_url+f'/api/v1/studies/executions/{producer}/model-fits/{fit["fit_name"]}/export',
            params={'expected_digest':digest},headers={'Authorization':'Bearer '+token})
        export.raise_for_status();artifact=base64.b64decode(export.json()['artifact_base64'],validate=True)
        state=decode_owned_ols(artifact,digest=digest,owner=owner,execution=producer)
        assert_fixture_state(state,args.fixture)
        (args.evidence/'original-artifact.bytes').write_bytes(artifact)
        write(args.evidence/'retained-ols-state.json',asdict(state))
        _,continuation=execute(native,fixture_source(args.fixture,stage='continue',producer_execution=producer),
            'Owned primary same-fit continuation',args.evidence/'continue',args.dispatch)
        table_answers(native,continuation,args.evidence/'continue',oracle['continuation'])
        effects(args.native_root,owner,producer,1,args.evidence/'fit-effects.json')
        effects(args.native_root,owner,continuation,0,args.evidence/'continue-effects.json')
        request={'request_id':str(uuid4()),'execution_id':producer,'fit_name':fit['fit_name'],
                 'expected_digest':digest,'source_location':'server','name':'Owned primary OLS exact reuse'}
        write(args.evidence/'save-intent.json',request)
        owner_jobs=args.native_root/'research_studies'/owner
        before=sorted(x.name for x in owner_jobs.iterdir())
        if args.drop_first_save_response:
            loss=LostSaveResponse(args.evidence/'observed-success-response-loss.json')
            with httpx.Client(timeout=90,trust_env=False,transport=loss) as lost_http:
                lost=EpochModels(base_url=platform_url,token=token,httpx_client=lost_http,follow_redirects=False)
                try:lost.save_model(**request,request_options=opts)
                except Exception:
                    if not loss.dropped or loss.observed_success is None:raise
                else:raise AssertionError('The controlled client unexpectedly received its hidden reply')
            observed=loss.observed_success
            if observed is None:raise ValueError('Genuinely unknown Save outcome: stop all writes')
            # A real successful response was deliberately observed by this
            # transport fixture. First reconcile its exact identity read-only.
            reconciled=models.read_model(observed.model_ref,request_options=opts)
            if (observed.digest!=digest or reconciled.model.digest!=digest
                    or reconciled.model.manifest_sha256!=observed.manifest_sha256):
                raise AssertionError('Read-only observed-success reconciliation changed the selected pins')
        # Either the initial known request or an explicitly bounded equal retry
        # after the controlled observed-success/read-only reconciliation above.
        saved=models.save_model(**request,request_options=opts)
        if args.drop_first_save_response:
            if (saved.model_ref!=observed.model_ref or saved.digest!=observed.digest
                    or saved.manifest_sha256!=observed.manifest_sha256):
                raise AssertionError('Known equal Save retry changed observed-success identity')
        else:
            repeated=models.save_model(**request,request_options=opts)
            if saved!=repeated:raise AssertionError('Known equal Save retry changed identity')
        if sorted(x.name for x in owner_jobs.iterdir())!=before:
            raise AssertionError('Save/retry started numerical work')
        facts=models.read_model(saved.model_ref,request_options=opts)
        if (facts.model.digest!=digest or facts.manifest.get('owner_user_id')!=owner
                or facts.model.producer_execution_id!=producer
                or facts.model.producer_config_sha256!=descriptor['producer_config_sha256']
                or facts.producer['study']['source']!=source):
            raise AssertionError('Actual saved resource lost the owned producer/source pins')
        write(args.evidence/'saved-summary.json',saved.model_dump(mode='json'))
        write(args.evidence/'saved-facts.json',facts.model_dump(mode='json'))
        selected=fixture_source(args.fixture,stage='saved',model_ref=saved.model_ref,
            model_manifest_sha256=saved.manifest_sha256)
        _,consumer=execute(native,selected,'Owned primary saved exact OLS consumer',args.evidence/'saved',args.dispatch)
        table_answers(native,consumer,args.evidence/'saved',oracle['continuation'])
        effects(args.native_root,owner,consumer,0,args.evidence/'saved-effects.json')
        config=args.native_root/'research_studies'/owner/consumer/'config.json'
        plan=json.loads(config.read_text())['execution_plan']
        selected_nodes=[json.loads(n['model_artifact_contract_json']) for n in plan['nodes']
                        if n['type']=='ols_infer']
        if len(selected_nodes)!=1:raise AssertionError('The saved consumer changed its exact fit roster')
        seal=selected_nodes[0]
        if (seal['resolved_binding']['digest']!=digest or seal['owner_user_id']!=owner
                or seal['saved_model_resource']['model_ref']!=saved.model_ref
                or seal['saved_model_resource']['manifest_sha256']!=saved.manifest_sha256):
            raise AssertionError('The executed saved consumer lost the exact immutable resource')
        receipt={'status':'passed','owner':owner,'dispatch':args.dispatch,'definition_id':definition,
            'producer_execution':producer,'continuation_execution':continuation,'saved_consumer_execution':consumer,
            'model_ref':saved.model_ref,'digest':digest,'manifest_sha256':saved.manifest_sha256,
            'native_url':native_url,'platform_url':platform_url,'native_root':str(args.native_root),
            'root_readiness':str(args.root_readiness),'root_readiness_sha256':readiness.sha256,
            'fixture_sha256':hashlib.sha256(args.fixture.read_bytes()).hexdigest(),
            'fit_count':1,'continuation_fit_count':0,'saved_consumer_fit_count':0,'save_numerical_executions':0,
            'scope':'actual admitted primary server-export OLS/API/Arrow lifecycle; no source cleanup, S3, AI/Portal/campaign claim'}
        write(args.evidence/'final-receipt.json',receipt)


def main() -> None:
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('native-url','platform-url','expected-owner','root-readiness-sha256'):
        parser.add_argument('--'+name,required=True)
    for name in ('native-root','fixture','evidence','root-readiness'):
        parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--dispatch',choices=['inprocess','subprocess'],default='inprocess')
    parser.add_argument('--drop-first-save-response',action='store_true')
    parser.add_argument('--execute',action='store_true')
    args=parser.parse_args()
    if not args.execute:parser.error('Actual root admission and explicit execute are required')
    run(args)


if __name__=='__main__':main()
