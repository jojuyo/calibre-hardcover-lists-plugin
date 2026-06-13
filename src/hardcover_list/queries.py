LIST_MEMBERSHIP_BY_SLUG = """
query HardcoverListMembershipBySlug($book_slug: String!) {
  me {
    lists(order_by: {created_at: desc}) {
      name
      slug
      list_books(
        where: {book: {slug: {_eq: $book_slug}, editions: {}}}
        limit: 1
      ) {
        book {
          title
        }
      }
    }
  }
}
"""

LIST_MEMBERSHIP_BY_ID = """
query HardcoverListMembershipById($book_id: Int!) {
  me {
    lists(order_by: {created_at: desc}) {
      name
      slug
      list_books(
        where: {book: {id: {_eq: $book_id}, editions: {}}}
        limit: 1
      ) {
        book {
          title
        }
      }
    }
  }
}
"""

USER_LISTS = """
query HardcoverUserLists {
  me {
    lists(order_by: {created_at: desc}) {
      id
      name
      slug
    }
  }
}
"""

CURRENT_USER_ID = """
query HardcoverCurrentUserId {
  me {
    id
  }
}
"""

# Fetch every book entry across all of the user's lists in one paginated stream.
ALL_LIST_BOOKS = """
query HardcoverAllListBooks($user_id: Int!, $limit: Int!, $offset: Int!) {
  list_books(
    where: {list: {user_id: {_eq: $user_id}}}
    order_by: {id: asc}
    limit: $limit
    offset: $offset
  ) {
    book_id
    book {
      slug
    }
    list {
      name
    }
  }
}
"""

# Resolve many edition ids to their canonical book in a single request.
BOOKS_BY_EDITIONS = """
query HardcoverBooksByEditions($ids: [Int!]!) {
  editions(where: {id: {_in: $ids}}) {
    id
    book {
      id
      slug
    }
  }
}
"""

BOOK_ID_BY_SLUG = """
query HardcoverBookIdBySlug($slug: String!) {
  books(where: {slug: {_eq: $slug}}, limit: 1) {
    id
  }
}
"""

BOOK_ID_BY_EDITION = """
query HardcoverBookIdByEdition($edition_id: Int!) {
  editions(where: {id: {_eq: $edition_id}}, limit: 1) {
    book {
      id
    }
  }
}
"""

LIST_BOOK_ENTRY = """
query HardcoverListBookEntry($list_id: Int!, $book_id: Int!) {
  list_books(
    where: {list_id: {_eq: $list_id}, book_id: {_eq: $book_id}}
    limit: 1
  ) {
    id
  }
}
"""

LIST_BOOK_ENTRIES = """
query HardcoverListBookEntries(
  $list_id: Int!
  $book_ids: [Int!]!
  $limit: Int!
  $offset: Int!
) {
  list_books(
    where: {list_id: {_eq: $list_id}, book_id: {_in: $book_ids}}
    order_by: {id: asc}
    limit: $limit
    offset: $offset
  ) {
    id
    book_id
  }
}
"""

INSERT_LIST_BOOK = """
mutation HardcoverInsertListBook(
  $list_id: Int!
  $book_id: Int!
  $edition_id: Int
) {
  insert_list_book(
    object: {list_id: $list_id, book_id: $book_id, edition_id: $edition_id}
  ) {
    id
  }
}
"""

INSERT_LIST = """
mutation HardcoverInsertList($name: String!) {
  insert_list(object: {name: $name}) {
    id
    errors
    list {
      id
      name
      slug
    }
  }
}
"""

DELETE_LIST_BOOK = """
mutation HardcoverDeleteListBook($id: Int!) {
  delete_list_book(id: $id) {
    id
  }
}
"""
