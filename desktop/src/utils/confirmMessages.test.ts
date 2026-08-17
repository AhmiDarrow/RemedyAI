import { describe, expect, it } from 'vitest'
import {
  projectRemoveConfirm,
  sessionDeleteConfirm,
  sessionsDeleteConfirm,
  skillDeleteConfirm,
} from './confirmMessages'

describe('sessionDeleteConfirm', () => {
  it('names the chat and speaks as Remedy', () => {
    const r = sessionDeleteConfirm('Grocery run')
    expect(r.title).toBe('Delete “Grocery run”?')
    expect(r.confirmLabel).toBe('Delete chat')
    // First person — not "Partner Memory is kept by the application"
    expect(r.body).toContain('What I remember about you')
    expect(r.body).toContain('Settings → You & Agent')
  })

  it('falls back for an untitled chat', () => {
    expect(sessionDeleteConfirm('   ').title).toBe('Delete “this chat”?')
    expect(sessionDeleteConfirm('').title).toBe('Delete “this chat”?')
  })
})

describe('sessionsDeleteConfirm', () => {
  it('uses singular copy for one', () => {
    const r = sessionsDeleteConfirm(1)
    expect(r.title).toBe('Delete 1 chat?')
    expect(r.confirmLabel).toBe('Delete chat')
  })

  it('pluralises and counts for many', () => {
    const r = sessionsDeleteConfirm(4)
    expect(r.title).toBe('Delete 4 chats?')
    expect(r.confirmLabel).toBe('Delete 4 chats')
  })
})

describe('projectRemoveConfirm', () => {
  it('uses the folder name and promises files are safe', () => {
    const r = projectRemoveConfirm('C:/Users/me/Old-Remedy', 3)
    expect(r.title).toBe('Remove “Old-Remedy” from the sidebar?')
    expect(r.body).toContain('Its 3 chats stay')
    expect(r.body).toContain('Nothing on disk is touched')
    expect(r.confirmLabel).toBe('Remove folder')
  })

  it('handles one chat and none', () => {
    expect(projectRemoveConfirm('/a/b/Proj', 1).body).toContain('Its 1 chat stay')
    expect(projectRemoveConfirm('/a/b/Proj', 0).body).toContain('It has no chats.')
  })

  it('handles windows and posix separators', () => {
    expect(projectRemoveConfirm('D:\\work\\Alongside', 0).title).toContain('Alongside')
    expect(projectRemoveConfirm('/home/me/site/', 0).title).toContain('site')
  })
})

describe('skillDeleteConfirm', () => {
  it('is first person and explains reinstall', () => {
    const r = skillDeleteConfirm('web-research')
    expect(r.title).toBe('Delete skill “web-research”?')
    expect(r.body).toContain('removes it from me')
    expect(r.body).toContain('reinstalled later')
    expect(r.confirmLabel).toBe('Delete skill')
  })
})
