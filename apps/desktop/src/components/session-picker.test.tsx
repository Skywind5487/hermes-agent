import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { I18nProvider } from '@/i18n/context'
import type { PaginatedSessions, SessionInfo } from '@/types/hermes'

import { SessionPickerDialog } from './session-picker'

vi.mock('@/hermes', async () => {
  const actual = await vi.importActual<typeof import('@/hermes')>('@/hermes')
  return { ...actual, listAllProfileSessions: vi.fn() }
})

import { listAllProfileSessions } from '@/hermes'

const mockedList = vi.mocked(listAllProfileSessions)

// cmdk measures its list with ResizeObserver, which jsdom does not provide.
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}

vi.stubGlobal('ResizeObserver', ResizeObserverStub)

function makeSession(id: string, title: string): SessionInfo {
  return {
    ended_at: null,
    id,
    input_tokens: 0,
    is_active: false,
    last_active: 0,
    message_count: 1,
    model: null,
    output_tokens: 0,
    preview: null,
    source: 'cli',
    started_at: 0,
    title,
    tool_call_count: 0
  } as SessionInfo
}

function makeResponse(sessions: SessionInfo[]): PaginatedSessions {
  return { sessions, total: sessions.length, limit: 200, offset: 0 }
}

const RECENT_RESPONSE = makeResponse([makeSession('recent-1', 'Recent One')])
const SEARCH_RESPONSE = makeResponse([makeSession('found-1', 'Found Match')])

function renderPicker(open = true, onResume = vi.fn()) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const onOpenChange = vi.fn()
  render(
    <QueryClientProvider client={queryClient}>
      <I18nProvider configClient={{ getConfig: async () => ({}), saveConfig: async () => ({ ok: true }) }}>
        <SessionPickerDialog
          activeStoredSessionId={null}
          onOpenChange={onOpenChange}
          onResume={onResume}
          open={open}
        />
      </I18nProvider>
    </QueryClientProvider>
  )
  return { onOpenChange, onResume }
}

describe('SessionPickerDialog', () => {
  beforeEach(() => {
    mockedList.mockReset()
  })

  afterEach(() => {
    cleanup()
  })

  it('loads recent sessions when the query is empty', async () => {
    mockedList.mockResolvedValue(RECENT_RESPONSE)

    renderPicker()

    await waitFor(() => {
      // Empty-query view: the recent-200 load, no search query passed.
      expect(mockedList).toHaveBeenCalledWith(200, 1, 'exclude')
    })
    expect(await screen.findByText('Recent One')).toBeDefined()
  })

  it('debounces typing into a whole-store server search with q', async () => {
    mockedList.mockImplementation(async (limit, minMessages, archived, order, profile, filter, q) =>
      q ? SEARCH_RESPONSE : RECENT_RESPONSE
    )

    renderPicker()
    const input = await screen.findByRole('combobox')
    fireEvent.change(input, { target: { value: 'an94' } })

    // After the debounce window the whole-store seam is asked with q; the
    // server results replace the client-filtered recent page.
    await waitFor(() => {
      expect(mockedList).toHaveBeenCalledWith(200, 1, 'exclude', 'recent', 'all', {}, 'an94')
    })
    expect(await screen.findByText('Found Match')).toBeDefined()
  })

  it('shows a search failure message when the server search errors', async () => {
    mockedList.mockImplementation(async (limit, minMessages, archived, order, profile, filter, q) => {
      if (q) {
        throw new Error('boom')
      }
      return RECENT_RESPONSE
    })

    renderPicker()
    const input = await screen.findByRole('combobox')
    fireEvent.change(input, { target: { value: 'an94' } })

    expect(
      await screen.findByText('Session search failed — check the backend and try again.')
    ).toBeDefined()
  })

  it('resumes the picked session and closes the dialog', async () => {
    const onResume = vi.fn()
    mockedList.mockResolvedValue(RECENT_RESPONSE)

    const { onOpenChange } = renderPicker(true, onResume)

    fireEvent.click(await screen.findByText('Recent One'))
    expect(onResume).toHaveBeenCalledWith('recent-1')
    expect(onOpenChange).toHaveBeenCalledWith(false)
  })

  it('discards a stale older search response in favor of the latest', async () => {
    // A slow 'a' search must not overwrite a newer 'ab' search that resolved
    // first — the query key carries the debounced term, so react-query
    // discards the stale in-flight response.
    const STALE_RESPONSE = makeResponse([makeSession('stale-1', 'Stale Match')])
    let resolveStale!: (v: PaginatedSessions) => void
    const stalePromise = new Promise<PaginatedSessions>(res => {
      resolveStale = res
    })

    mockedList.mockImplementation(async (limit, minMessages, archived, order, profile, filter, q) => {
      if (q === 'a') {
        return stalePromise
      }
      if (q === 'ab') {
        return SEARCH_RESPONSE
      }
      return RECENT_RESPONSE
    })

    renderPicker()
    const input = await screen.findByRole('combobox')
    fireEvent.change(input, { target: { value: 'a' } })
    await waitFor(() => {
      expect(mockedList).toHaveBeenCalledWith(200, 1, 'exclude', 'recent', 'all', {}, 'a')
    })

    fireEvent.change(input, { target: { value: 'ab' } })
    await waitFor(() => {
      expect(mockedList).toHaveBeenCalledWith(200, 1, 'exclude', 'recent', 'all', {}, 'ab')
    })
    expect(await screen.findByText('Found Match')).toBeDefined()

    // The stale 'a' response resolves AFTER 'ab' — it must not replace the
    // newer results.
    await act(async () => {
      resolveStale(STALE_RESPONSE)
    })
    await waitFor(() => {
      expect(screen.queryByText('Stale Match')).toBeNull()
    })
    expect(screen.getByText('Found Match')).toBeDefined()
  })
})
