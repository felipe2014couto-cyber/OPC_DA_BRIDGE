// SPDX-License-Identifier: MIT
//! Independent dotted Item ID navigation. No COM or third-party definitions.
use std::collections::BTreeSet;

#[derive(Clone, Debug)]
pub struct Entry {
    pub item_id: String,
    pub data_type: u16,
    pub access_rights: u32,
}

#[derive(Default)]
pub struct Position(String);

#[derive(Debug, PartialEq)]
pub enum BrowseError { AboveRoot, InvalidArgument }

impl Position {
    pub fn path(&self) -> &str { &self.0 }

    fn suffix<'a>(&self, id: &'a str) -> Option<&'a str> {
        if self.0.is_empty() { Some(id) }
        else { id.strip_prefix(&self.0)?.strip_prefix('.') }
    }

    fn branch(path: &str, entries: &[Entry]) -> bool {
        path.is_empty() || entries.iter().any(|e| {
            e.item_id.strip_prefix(path).and_then(|s| s.strip_prefix('.'))
                .is_some_and(|s| !s.is_empty())
        })
    }

    /// 1=up, 2=down one relative branch, 3=absolute branch (empty=root).
    pub fn change(&mut self, direction: i32, destination: &str, entries: &[Entry]) -> Result<(), BrowseError> {
        let next = match direction {
            1 => {
                if self.0.is_empty() { return Err(BrowseError::AboveRoot); }
                self.0.rsplit_once('.').map_or("", |(parent, _)| parent).to_owned()
            }
            2 if !destination.is_empty() && !destination.contains('.') => {
                if self.0.is_empty() { destination.to_owned() }
                else { format!("{}.{}", self.0, destination) }
            }
            3 => destination.to_owned(),
            _ => return Err(BrowseError::InvalidArgument),
        };
        if !Self::branch(&next, entries) { return Err(BrowseError::InvalidArgument); }
        self.0 = next;
        Ok(())
    }

    /// 1=immediate branches, 2=immediate leaves, 3=recursive full Item IDs.
    pub fn enumerate(&self, kind: i32, filter: &str, data_type: u16, rights: u32, entries: &[Entry]) -> Result<Vec<String>, BrowseError> {
        if !(1..=3).contains(&kind) { return Err(BrowseError::InvalidArgument); }
        let mut result = BTreeSet::new();
        for entry in entries {
            let Some(suffix) = self.suffix(&entry.item_id) else { continue };
            if suffix.is_empty() { continue; }
            if kind == 1 {
                if let Some((branch, rest)) = suffix.split_once('.') {
                    if !branch.is_empty() && !rest.is_empty() && pattern_matches(filter, branch) {
                        result.insert(branch.to_owned());
                    }
                }
            } else {
                if kind == 2 && suffix.contains('.') { continue; }
                if data_type != 0 && entry.data_type != data_type { continue; }
                if rights != 0 && entry.access_rights & rights != rights { continue; }
                let name = if kind == 3 { &entry.item_id } else { suffix };
                if pattern_matches(filter, name) { result.insert(name.to_owned()); }
            }
        }
        Ok(result.into_iter().collect())
    }

    pub fn resolve(&self, name: &str, entries: &[Entry]) -> Result<String, BrowseError> {
        // Flat results are already fully qualified; never rewrite their IDs.
        if entries.iter().any(|e| e.item_id == name) { return Ok(name.to_owned()); }
        let candidate = if name.is_empty() { self.0.clone() }
            else if self.0.is_empty() { name.to_owned() }
            else { format!("{}.{}", self.0, name) };
        if Self::branch(&candidate, entries) || entries.iter().any(|e| e.item_id == candidate) {
            Ok(candidate)
        } else { Err(BrowseError::InvalidArgument) }
    }
}

pub fn pattern_matches(pattern: &str, value: &str) -> bool {
    if pattern.is_empty() || pattern == "*" { return true; }
    let pattern: Vec<char> = pattern.chars().map(|c| c.to_ascii_lowercase()).collect();
    let value: Vec<char> = value.chars().map(|c| c.to_ascii_lowercase()).collect();
    let mut previous = vec![false; value.len() + 1]; previous[0] = true;
    for token in pattern {
        let mut current = vec![false; value.len() + 1];
        if token == '*' {
            current[0] = previous[0];
            for i in 1..=value.len() { current[i] = previous[i] || current[i - 1]; }
        } else {
            for i in 1..=value.len() { current[i] = previous[i - 1] && (token == '?' || token == value[i - 1]); }
        }
        previous = current;
    }
    previous[value.len()]
}

#[cfg(test)]
mod tests {
    use super::*;
    fn catalog() -> Vec<Entry> {
        ["RB1PI1.TESTE.TESTE1.DIGITAL", "RB1PI1.TESTE.TESTE1.PRESSAO",
         "RB1PI1.TESTE.TESTE1.TESTE1", "RB1PI1.TESTE.TESTE1.TESTE2",
         "LFS_TD1_PB2.RB1PI2.Current.Velocidade"].iter().map(|id| Entry {
            item_id: (*id).into(), data_type: if id.ends_with("DIGITAL") {11} else {5}, access_rights: 1
        }).collect()
    }
    #[test] fn branch_leaf_and_flat_are_distinct() {
        let e=catalog(); let mut p=Position::default();
        assert_eq!(p.enumerate(1,"",0,0,&e).unwrap(), ["LFS_TD1_PB2","RB1PI1"]);
        assert!(p.enumerate(2,"",0,0,&e).unwrap().is_empty());
        assert_eq!(p.enumerate(3,"",0,0,&e).unwrap().len(),5);
        for branch in ["LFS_TD1_PB2","RB1PI2","Current"] { p.change(2,branch,&e).unwrap(); }
        assert_eq!(p.enumerate(2,"",0,0,&e).unwrap(),["Velocidade"]);
        assert_eq!(p.resolve("Velocidade",&e).unwrap(),"LFS_TD1_PB2.RB1PI2.Current.Velocidade");
        assert_eq!(p.enumerate(3,"",0,0,&e).unwrap(),["LFS_TD1_PB2.RB1PI2.Current.Velocidade"]);
    }
    #[test] fn up_to_root_terminates_and_invalid_moves_are_atomic() {
        let e=catalog();let mut p=Position::default();
        p.change(3,"RB1PI1.TESTE.TESTE1",&e).unwrap();
        for _ in 0..3 { p.change(1,"",&e).unwrap(); }
        for _ in 0..8 {assert_eq!(p.change(1,"",&e),Err(BrowseError::AboveRoot));}
        for (d,s) in [(0,""),(2,"missing"),(2,"RB1PI1.TESTE"),(3,"RB1PI1.TESTE.TESTE1.TESTE2")] {
            assert_eq!(p.change(d,s,&e),Err(BrowseError::InvalidArgument));assert_eq!(p.path(),"");
        }
    }
    #[test] fn filters_do_not_hide_navigation_branches() {
        let e=catalog();let mut p=Position::default();
        assert_eq!(p.enumerate(1,"R*",11,2,&e).unwrap(),["RB1PI1"]);
        p.change(3,"RB1PI1.TESTE.TESTE1",&e).unwrap();
        assert_eq!(p.enumerate(2,"*",11,1,&e).unwrap(),["DIGITAL"]);
        assert_eq!(p.enumerate(2,"TESTE?",5,1,&e).unwrap(),["TESTE1","TESTE2"]);
        assert!(p.enumerate(2,"*",0,2,&e).unwrap().is_empty());
    }
    #[test] fn clients_have_independent_positions_and_full_ids_are_stable() {
        let e=catalog();let mut a=Position::default();let b=Position::default();
        a.change(3,"LFS_TD1_PB2.RB1PI2.Current",&e).unwrap();
        assert_eq!(b.path(),"");
        assert_eq!(a.resolve("RB1PI1.TESTE.TESTE1.DIGITAL",&e).unwrap(),e[0].item_id);
        assert_eq!(a.resolve("",&e).unwrap(),a.path());
        assert!(a.resolve("missing",&e).is_err());
    }
    #[test] fn prefix_collision_and_branch_with_value_are_unambiguous() {
        let e:Vec<Entry>=["A","A.X","AB.Y"].iter().map(|s|Entry{item_id:(*s).into(),data_type:5,access_rights:1}).collect();
        let mut p=Position::default();assert_eq!(p.enumerate(2,"",0,0,&e).unwrap(),["A"]);
        p.change(2,"A",&e).unwrap();assert_eq!(p.enumerate(3,"",0,0,&e).unwrap(),["A.X"]);
    }
}
