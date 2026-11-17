---
title: "Reader and curator acceptance criteria"
project_id: paperflow
doc_uuid: b5b56de7-1cd0-4a3b-a7bf-714a4cc7798c
id: reader-acceptance
tags: [acceptance]
---

# Reader and curator acceptance criteria

This initial acceptance baseline follows the [release slices](reader-release.md).
Backend criteria establish foundations, not browser readiness. Full publication also
requires the remaining operational and delivery checks in that release plan.
Browser identity is outside the local MVP.

## FR-1: Browse ingested channels

### AC-1: Browse ingested channels

Requirement: FR-1

Given two ingested channels, when an explorer browses one with limit and offset, then only that channel appears, pages do not overlap, and a missing channel is empty.

## FR-2: Read with source context

### AC-2: Read with source context

Requirement: FR-2

Given a long revision and a discussion observation, when a reader opens a compact item then a bounded preview is returned; drill-down returns the complete hash-consistent revision and the observation preserves article and discussion URLs, sample status, and unknown scores.

## FR-3: Collect research durably without identity

### AC-3: Collect research durably without identity

Requirement: FR-3

Given a local service without identity, when a curator retries adding an item to a research bucket and restarts the service, then there is one membership; replay returns the same result and changing the payload under the same key returns conflict.

## FR-4: Review evidence without losing its basis

### AC-4: Review evidence without losing its basis

Requirement: FR-4

Given an exact quotation, when a curator attaches challenging evidence and reviews it, then export retains its stance, review status, source URL and revision ID; a later source revision cannot change the old quotation and mismatched revision evidence is rejected.

## FR-5: Save and resume everyday reading

### AC-5: Save and resume everyday reading

Requirement: FR-5

Given a reader on Today, when they save an item, restart and visit Saved, then the item and reading position persist and opening it resumes at that position.

## FR-6: Read accessibly across devices

### AC-6: Read accessibly across devices

Requirement: FR-6

Given the Option D browser surface, when a reader navigates Today, Saved and Research using only a keyboard and a screen reader at narrow widths and 200 percent zoom, then focus remains visible and logical, controls have accessible names, content reflows without horizontal text scrolling, and reading preferences persist. Verify WCAG 2.2 AA with automated and manual checks; record results and remaining defects.
