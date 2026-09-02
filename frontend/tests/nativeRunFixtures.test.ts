import assert from 'node:assert/strict';
import test from 'node:test';

import {
  NATIVE_AUDIT_DETAIL_CREDENTIAL_SENTINELS,
  NATIVE_RUN_PRIVATE_SENTINELS,
  nativeAuditDetailFixture,
  nativeRunFailureFixture,
  nativeRunPageTwoFirstFixture,
  nativeRunPartialFixture,
  nativeRunSuccessFixture,
} from './nativeRunFixtures';
import { buildPresentationPreview } from '../src/features/presentationPreview/presentationPreview';
import type { RunDetail } from '../src/types';

test('Native Run fixtures preserve the serialized success/failure contract and exclude private sentinels', () => {
  assert.equal(nativeRunSuccessFixture.codex_audit.invocations[0].native_image.terminal_state, 'result_received');
  assert.equal(nativeRunSuccessFixture.slides[0].final_image_path, '/artifacts/runs/run-901/slide-01-business.png');
  assert.equal(nativeRunFailureFixture.codex_audit.invocations[0].native_image.failure_code, 'image_call_binding_failed');
  assert.equal(nativeRunFailureFixture.slides[0].final_image_path, null);

  const serialized = JSON.stringify([
    nativeRunSuccessFixture,
    nativeRunFailureFixture,
    nativeRunPageTwoFirstFixture,
    nativeRunPartialFixture,
  ]);
  for (const sentinel of NATIVE_RUN_PRIVATE_SENTINELS) {
    assert.equal(serialized.includes(sentinel), false, `fixture leaked private sentinel: ${sentinel}`);
  }
});

test('Native audit detail fixtures keep business evidence and linkage while excluding credentials', () => {
  assert.equal(nativeAuditDetailFixture.lineage.run_id, nativeRunSuccessFixture.id);
  assert.equal(nativeAuditDetailFixture.lineage.run_slide_id, nativeRunSuccessFixture.slides[0].id);
  assert.equal(nativeAuditDetailFixture.lineage.call.id, 'NIMG050F_AUDIT_IMAGEGEN_CALL');
  assert.equal(nativeAuditDetailFixture.tool_calls[0].name, 'imagegen');
  assert.match(nativeAuditDetailFixture.prompt || '', /business prompt/);
  assert.match(nativeAuditDetailFixture.assistant_output || '', /business image request/);

  const detailSerialized = JSON.stringify(nativeAuditDetailFixture);
  for (const sentinel of [...NATIVE_AUDIT_DETAIL_CREDENTIAL_SENTINELS, ...NATIVE_RUN_PRIVATE_SENTINELS]) {
    assert.equal(detailSerialized.includes(sentinel), false, `detail fixture leaked private sentinel: ${sentinel}`);
  }
});

test('Image 3.0 Preview keeps deck order while page 2 is the first displayable result', () => {
  const preview = buildPresentationPreview(nativeRunPageTwoFirstFixture);

  assert.deepEqual(preview.slides.map((slide) => slide.position), [1, 2, 3, 4]);
  assert.deepEqual(preview.slides.map((slide) => slide.title), ['封面', '第二页', '第三页', '第四页']);
  assert.equal(preview.slides[0].position, 1, 'the confirmed cover must remain first');
  assert.deepEqual(
    preview.slides.filter((slide) => slide.displayable).map((slide) => slide.position),
    [2],
  );
  assert.equal(preview.shouldPoll, true);
  assert.equal(preview.downloadEnabled, false);
});

test('Image 3.0 partial Preview keeps final deck order and displayable successes', () => {
  const preview = buildPresentationPreview(nativeRunPartialFixture);

  assert.deepEqual(preview.slides.map((slide) => slide.position), [1, 2, 3, 4]);
  assert.deepEqual(
    preview.slides.filter((slide) => slide.displayable).map((slide) => slide.position),
    [1, 2, 4],
  );
  assert.equal(preview.statusTone, 'warning');
  assert.equal(preview.shouldPoll, false);
  assert.equal(preview.downloadEnabled, true);
});

test('Public Image 3.0 Preview accepts only public final PNG artifacts', () => {
  const run = {
    ...nativeRunPageTwoFirstFixture,
    status: 'completed',
    completed_at: '2026-07-30T00:01:00Z',
    slides: [
      {
        ...nativeRunPageTwoFirstFixture.slides[0],
        position: 1,
        slide_title: 'Legacy HTML fallback',
        final_image_path: null,
        screenshot_path: '/artifacts/public/run-903/01.png',
        clean_html: '<section>private legacy HTML</section>',
        html_path: '/artifacts/public/run-903/01.html',
        active_version: {
          id: 1,
          target_run_slide_id: 9031,
          artifact_run_slide_id: 9031,
          version_number: 1,
          status: 'active',
          final_image_path: '/artifacts/public/run-903/01-old-version.png',
          screenshot_path: '/artifacts/public/run-903/01-old-version.png',
        },
      },
      {
        ...nativeRunPageTwoFirstFixture.slides[1],
        position: 2,
        slide_title: 'Public Image 3.0 PNG',
        final_image_path: '/artifacts/public/run-903/02.png',
        screenshot_path: '/artifacts/public/run-903/02-screenshot.png',
      },
      {
        ...nativeRunPageTwoFirstFixture.slides[2],
        position: 3,
        slide_title: 'Private artifact',
        final_image_path: '/artifacts/.codex-private/run-903/03.png',
        screenshot_path: '/artifacts/public/run-903/03.png',
      },
      {
        ...nativeRunPageTwoFirstFixture.slides[3],
        position: 4,
        slide_title: 'Non-PNG artifact',
        final_image_path: '/artifacts/public/run-903/04.html',
        screenshot_path: '/artifacts/public/run-903/04.png',
      },
    ],
  } as unknown as RunDetail;

  const preview = buildPresentationPreview(run);

  assert.deepEqual(
    preview.slides.filter((slide) => slide.displayable).map((slide) => slide.position),
    [2],
  );
  assert.equal(preview.slides.find((slide) => slide.position === 2)?.artifactPath, '/artifacts/public/run-903/02.png');
  for (const position of [1, 3, 4]) {
    const slide = preview.slides.find((candidate) => candidate.position === position);
    assert.equal(slide?.artifactPath, null, `slide ${position} must not use a legacy/private fallback`);
    assert.equal(slide?.displayable, false);
  }
  assert.equal(preview.downloadEnabled, true);
  assert.equal(JSON.stringify(preview).includes('private legacy HTML'), false);
  assert.equal(JSON.stringify(preview).includes('old-version'), false);
});
